#!/usr/bin/env python3
"""
Enhanced RAG Processor for WordPress Chatbot
Handles both PDF documents and trained websites with excellent conversational responses
"""

import os
import sys
import json
import re
import requests
from pathlib import Path
from PyPDF2 import PdfReader
import chromadb
import hashlib
import time
import urllib3
from bs4 import BeautifulSoup
import trafilatura
try:
    import html2text as _html2text_module
except ImportError:
    _html2text_module = None
from urllib.parse import urlparse, urljoin

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

class RAGProcessor:
    def __init__(self, pdf_path=None, db_path=None, openai_api_key=None):
        self.pdf_path = pdf_path
        self.db_path  = db_path or os.path.join(os.path.dirname(__file__), 'vector_db')
        self.openai_api_key = openai_api_key or os.environ.get('OPENAI_API_KEY', '')
        self.openai_model   = 'gpt-4o'
        self.openai_url     = 'https://api.openai.com/v1/chat/completions'
        os.makedirs(self.db_path, exist_ok=True)
        self.chroma_client       = chromadb.PersistentClient(path=self.db_path)
        self.all_docs_collection = 'all_company_documents'
        if self.openai_api_key:
            print(f'  ✅ AI Backend: OpenAI {self.openai_model}')
        else:
            print('  ⚠️  No OpenAI key provided — queries will fail.')

    # ─────────────────────────────────────────────
    # PDF Processing
    # ─────────────────────────────────────────────

    def extract_text_from_pdf(self, pdf_path):
        print(f"📄 Extracting text from: {pdf_path}")
        try:
            reader = PdfReader(pdf_path)
            text = ""
            pages_text = []
            for page_num, page in enumerate(reader.pages):
                page_text = page.extract_text()
                if page_text:
                    page_text = re.sub(r'\s+', ' ', page_text).strip()
                    pages_text.append(page_text)
                    text += f"\n\n[PAGE {page_num + 1}]\n{page_text}"
                    if page_num == 0:
                        print(f"  Sample from page 1: {page_text[:150]}...")
            print(f"✅ Extracted {len(text)} characters from {len(reader.pages)} pages")
            return text, pages_text
        except Exception as e:
            print(f"❌ Error extracting PDF: {e}")
            return None, None

    def clean_document_text(self, text):
        text = re.sub(r'\[PAGE \d+\]', '', text)
        for ch in ['•', '✓', '✅']:
            text = text.replace(ch, '')
        return re.sub(r'\s+', ' ', text).strip()

    def _save_raw_pdf_text(self, pdf_name, text):
        """Cache full raw PDF text so keyword_search can scan it directly."""
        safe = re.sub(r'[^a-z0-9]', '_', pdf_name.lower())
        path = os.path.join(self.db_path, f'raw_{safe}.txt')
        try:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(text)
            print(f"  📝 Raw text cached: raw_{safe}.txt")
        except Exception as e:
            print(f"  ⚠️  Raw cache failed: {e}")

    def _load_raw_pdf_texts(self):
        """Load all cached raw PDF texts from disk."""
        out = []
        try:
            for fname in os.listdir(self.db_path):
                if fname.startswith('raw_') and fname.endswith('.txt'):
                    with open(os.path.join(self.db_path, fname), 'r', encoding='utf-8') as f:
                        out.append((fname, f.read()))
        except Exception:
            pass
        return out

    # ── STAFF-AWARE CHUNKER ───────────────────────────────────────────────────
    def smart_chunk_text(self, text, pages_text, pdf_name):
        """
        Keeps each staff member's complete record (name + role + phone +
        email + responsibilities) as one atomic chunk. Never splits mid-record.
        """
        raw_blocks = re.split(r'\n{2,}', text)

        # Merge consecutive paragraph blocks into logical sections.
        # Start a new section when we see a person-name header or an ALL-CAPS section header.
        merged = []
        current = []

        def is_person_header(blk):
            line = blk.strip().split('\n')[0].strip()
            return (bool(re.match(r'^[A-Z][a-z]+ [A-Z][a-z]', line))
                    and ':' not in line
                    and not line.isupper()
                    and len(line.split()) <= 5)

        def is_section_header(blk):
            line = blk.strip().split('\n')[0].strip()
            return bool(re.match(r'^[A-Z][A-Z\s&/]{3,}$', line)) and len(line) < 60

        for blk in raw_blocks:
            blk = blk.strip()
            if not blk:
                continue
            if is_person_header(blk) or is_section_header(blk):
                if current:
                    merged.append('\n\n'.join(current))
                current = [blk]
            else:
                current.append(blk)
        if current:
            merged.append('\n\n'.join(current))

        chunks = []
        staff_found = 0

        for block in merged:
            block = block.strip()
            if len(block) < 40:
                continue
            is_staff = bool(re.search(
                r'(?:Role|Contact Number|Phone|Email|Department)\s*:', block, re.I))
            chunk = {
                'text': self.clean_document_text(block),
                'raw_text': block,
                'source': pdf_name,
                'page': self._guess_page(block, text),
                'source_type': 'pdf',
                'type': 'staff_record' if is_staff else 'section'
            }
            if len(chunk['text']) > 30:
                chunks.append(chunk)
                if is_staff:
                    staff_found += 1

        # Fallback: paragraph windowing if too few chunks
        if len(chunks) < 3:
            paras = [p.strip() for p in text.split('\n\n') if p.strip()]
            cur, cl = [], 0
            for para in paras:
                wc = len(para.split())
                if cl + wc <= 350:
                    cur.append(para); cl += wc
                else:
                    if cur:
                        ct = ' '.join(cur)
                        chunks.append({'text': self.clean_document_text(ct), 'raw_text': ct,
                                       'type': 'paragraph', 'source': pdf_name,
                                       'page': self._guess_page(ct, text), 'source_type': 'pdf'})
                    cur, cl = [para], wc
            if cur:
                ct = ' '.join(cur)
                chunks.append({'text': self.clean_document_text(ct), 'raw_text': ct,
                               'type': 'paragraph', 'source': pdf_name,
                               'page': self._guess_page(ct, text), 'source_type': 'pdf'})

        # Deduplicate
        seen, unique = set(), []
        for c in chunks:
            key = c['text'][:80].strip()
            if key not in seen and len(c['text']) > 30:
                seen.add(key); unique.append(c)

        print(f"  Staff records: {staff_found}")
        print(f"✅ Created {len(unique)} smart chunks from PDF")
        return unique

    def _guess_page(self, chunk_text, full_text):
        page_markers = list(re.finditer(r'\[PAGE (\d+)\]', full_text))
        best_page = 1
        chunk_pos = full_text.find(chunk_text[:100])
        if chunk_pos >= 0:
            for marker in page_markers:
                if marker.start() < chunk_pos:
                    best_page = int(marker.group(1))
        return best_page

    # ── KEYWORD SEARCH: raw-PDF-first exact matching ──────────────────────────
    def keyword_search(self, question):
        """
        Stage 1 — scan raw PDF text files directly (most reliable, bypasses
                   any chunk boundary issues).
        Stage 2 — scan ChromaDB chunks (catches website content too).
        PDF results always come first.
        """
        q = question.lower()

        # Role → search terms
        role_map = {
            'property manager':   ['property manager', 'daniel thomas'],
            'leasing manager':    ['leasing manager', 'imran qureshi'],
            'operations manager': ['operations manager', 'sarah khan'],
            'managing director':  ['managing director', 'ahmed al mansoori'],
            'sales':              ['sales department', 'mohammed rafiq', 'fatima noor'],
            'finance':            ['finance', 'accounts', 'priya sharma'],
            'leasing executive':  ['leasing executive', 'aisha rahman'],
            'timing':             ['office hours', 'working hours', 'office timing',
                                   'sunday', 'monday', '9:00', '6:00'],
            'location':           ['floor', 'tower', 'business bay', 'address',
                                   'empire heights', 'located'],
        }

        target_terms = []
        for role, kws in role_map.items():
            if role in q or any(kw in q for kw in kws):
                target_terms.extend(kws)

        if not target_terms:
            stop = {'who','what','where','when','how','are','the','is','a','an',
                    'of','in','at','for','to','do','does','tell','me','about',
                    'our','your','their','company'}
            target_terms = [w for w in re.findall(r'[a-z]+', q)
                            if len(w) >= 3 and w not in stop]

        results = []

        # ── Stage 1: raw PDF text (guaranteed complete records) ───────────────
        for fname, full_text in self._load_raw_pdf_texts():
            tl = full_text.lower()
            for term in target_terms:
                idx = tl.find(term)
                if idx == -1:
                    continue
                # Walk back to paragraph start
                start = max(0, idx - 400)
                seg   = full_text[start:idx]
                lb    = seg.rfind('\n\n')
                if lb != -1:
                    start = start + lb + 2
                # Walk forward ~800 chars (covers full staff record)
                end      = min(len(full_text), idx + 800)
                seg_fwd  = full_text[idx:end]
                blanks   = [m.start() for m in re.finditer(r'\n\n', seg_fwd)]
                if len(blanks) >= 2:
                    end = idx + blanks[1]
                elif blanks:
                    end = idx + blanks[0] + 300
                block = full_text[start:end].strip()
                if block and len(block) > 40:
                    results.append(block)
            print(f"  INFO: Raw PDF '{fname}' scanned → {len(results)} blocks")

        # ── Stage 2: ChromaDB chunks ──────────────────────────────────────────
        try:
            col   = self.chroma_client.get_collection(self.all_docs_collection)
            count = col.count()
            if count > 0:
                all_data = col.get(limit=count, include=["documents", "metadatas"])
                pdf_hits, web_hits = [], []
                for doc, meta in zip(all_data['documents'], all_data['metadatas']):
                    dl    = doc.lower()
                    score = sum(1 for t in target_terms if t in dl)
                    if score == 0:
                        continue
                    st  = str(meta.get('source_type', '')).lower()
                    src = str(meta.get('source', '')).lower()
                    if 'pdf' in st or '.pdf' in src:
                        pdf_hits.append((score, doc))
                    else:
                        web_hits.append((score, doc))
                pdf_hits.sort(key=lambda x: -x[0])
                web_hits.sort(key=lambda x: -x[0])
                results += [d for _, d in pdf_hits[:6]]
                results += [d for _, d in web_hits[:4]]
        except Exception:
            pass

        # Deduplicate
        seen, unique = set(), []
        for r in results:
            key = r[:60].strip()
            if key not in seen:
                seen.add(key); unique.append(r)

        return unique

    # ─────────────────────────────────────────────
    # Website Processing
    # ─────────────────────────────────────────────

    CLOUDFLARE_SITE_PAGES = {
        'dxbinteract.com': [
            'https://dxbinteract.com/projects/silver-tower',
            'https://dxbinteract.com/projects/university-view',
            'https://dxbinteract.com/projects/jomana-8',
            'https://dxbinteract.com/projects/marina-pinnacle',
            'https://dxbinteract.com/projects/damac-towers-by-paramount',
        ],
    }

    CLOUDFLARE_HEADERS = {
        'User-Agent':      'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        'Accept':          'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Cache-Control':   'no-cache',
        'Pragma':          'no-cache',
        'Sec-Ch-Ua':       '"Chromium";v="122", "Not(A:Brand";v="24"',
        'Sec-Ch-Ua-Mobile':'?0',
        'Sec-Ch-Ua-Platform': '"macOS"',
        'Sec-Fetch-Dest':  'document',
        'Sec-Fetch-Mode':  'navigate',
        'Sec-Fetch-Site':  'none',
        'Sec-Fetch-User':  '?1',
        'Upgrade-Insecure-Requests': '1',
    }

    def _fetch_cloudflare_site(self, url):
        try:
            parsed   = urlparse(url)
            base_url = f"{parsed.scheme}://{parsed.netloc}"
            session  = requests.Session()
            headers  = dict(self.CLOUDFLARE_HEADERS)
            print(f"     🔐 CF warm-up: {base_url}")
            session.get(base_url, headers=headers, timeout=20, verify=False)
            time.sleep(2)
            headers['Referer'] = base_url
            resp = session.get(url, headers=headers, timeout=25, verify=False)
            if resp.status_code == 200:
                print(f"     ✅ CF fetch OK ({len(resp.text)} chars)")
                return resp.text
            else:
                print(f"     ❌ CF fetch returned {resp.status_code}")
                return None
        except Exception as e:
            print(f"     ❌ CF fetch error: {e}")
            return None

    def extract_website_content(self, url, max_pages=10):
        print(f"\n🌐 Starting crawl: {url}")
        visited_urls = set()
        all_content  = []
        try:
            parsed      = urlparse(url)
            base_domain = parsed.netloc
            base_url    = f"{parsed.scheme}://{parsed.netloc}"
        except Exception:
            base_domain = url.split('/')[2] if '://' in url else url.split('/')[0]
            base_url    = url

        is_cf_protected = any(cf in base_domain for cf in self.CLOUDFLARE_SITE_PAGES)
        if is_cf_protected:
            print(f"  🛡️  Cloudflare-protected site: {base_domain}")
            cf_key   = next(k for k in self.CLOUDFLARE_SITE_PAGES if k in base_domain)
            cf_pages = [url] + self.CLOUDFLARE_SITE_PAGES[cf_key]
            pages_read = 0
            for cf_url in cf_pages:
                if pages_read >= max_pages:
                    break
                print(f"  📄 [{pages_read+1}] {cf_url}")
                html_text = self._fetch_cloudflare_site(cf_url)
                if html_text:
                    soup  = BeautifulSoup(html_text, 'html.parser')
                    title = soup.title.string.strip()[:200] if soup.title and soup.title.string else ''
                    self._process_page_content(html_text, soup, cf_url, title, all_content)
                    pages_read += 1
                    visited_urls.add(cf_url)
                time.sleep(2)
            print(f"\n✅ CF crawl complete: {pages_read} pages")
            return all_content

        to_visit = [url]
        valuable_paths = [
            '/services', '/services-property-management', '/our-services',
            '/about', '/about-us', '/technology', '/tech',
            '/contact', '/contact-us', '/team', '/managers',
            '/blog', '/faq', '/pricing', '/properties',
            '/MMP_managing_application', '/mmp_property_managers', '/blog_25',
        ]
        for path in valuable_paths:
            candidate = base_url.rstrip('/') + path
            if candidate not in to_visit:
                to_visit.append(candidate)

        user_agents = [
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_3) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15',
        ]
        ua_index   = 0
        pages_read = 0

        while to_visit and pages_read < max_pages:
            current_url = to_visit.pop(0)
            if current_url in visited_urls:
                continue
            visited_urls.add(current_url)
            print(f"  📄 [{pages_read+1}/{max_pages}] {current_url}")

            html_text = None
            status    = None

            for attempt in range(len(user_agents)):
                ua = user_agents[(ua_index + attempt) % len(user_agents)]
                headers = {
                    'User-Agent': ua, 'Accept': 'text/html,*/*;q=0.8',
                    'Accept-Language': 'en-US,en;q=0.9',
                    'Connection': 'keep-alive', 'Cache-Control': 'no-cache',
                }
                try:
                    resp = requests.get(current_url, headers=headers, timeout=25,
                                        verify=False, allow_redirects=True)
                    status = resp.status_code
                    if status == 200:
                        html_text = resp.text
                        ua_index  = (ua_index + attempt) % len(user_agents)
                        break
                    elif status in (403, 429):
                        time.sleep(1.5)
                    else:
                        break
                except requests.exceptions.Timeout:
                    pass
                except Exception as e:
                    print(f"     ⚠️ {e}")

            if html_text is None and status in (403, 429):
                try:
                    session = requests.Session()
                    session.headers.update({'User-Agent': user_agents[0], 'Referer': base_url})
                    session.get(base_url, timeout=15, verify=False)
                    time.sleep(1)
                    resp2 = session.get(current_url, timeout=25, verify=False)
                    if resp2.status_code == 200:
                        html_text = resp2.text
                except Exception:
                    pass

            if html_text is None:
                continue

            soup  = BeautifulSoup(html_text, 'html.parser')
            title = soup.title.string.strip()[:200] if soup.title and soup.title.string else ''
            self._process_page_content(html_text, soup, current_url, title, all_content)
            pages_read += 1

            links_added = 0
            for link in soup.find_all('a', href=True):
                if links_added >= 20:
                    break
                href = link['href'].strip()
                if not href or href.startswith('#') or href.startswith('mailto:') or href.startswith('tel:'):
                    continue
                if href.startswith('/'):
                    full_url = base_url.rstrip('/') + href
                elif href.startswith('http'):
                    full_url = href
                else:
                    full_url = urljoin(current_url, href)

                skip = ['.pdf','.jpg','.png','.gif','.zip','.mp4','.svg','.webp','.ico',
                        'login','signup','cart','checkout','logout','wp-admin','wp-login',
                        'feed','.xml','sitemap','facebook','twitter','instagram','linkedin']
                if (base_domain in full_url and full_url not in visited_urls
                        and full_url not in to_visit and '#' not in full_url
                        and not any(x in full_url.lower() for x in skip)):
                    to_visit.append(full_url)
                    links_added += 1

            time.sleep(0.8)

        print(f"\n✅ Crawl complete: {pages_read} pages, {len(all_content)} items")
        return all_content

    def _process_page_content(self, html_text, soup, url, title, all_content):
        content_added = 0
        try:
            traf_text = trafilatura.extract(html_text, include_comments=False,
                                            include_tables=True, no_fallback=False,
                                            favor_recall=True)
            if traf_text and len(traf_text.strip()) > 80:
                traf_clean = re.sub(r'\s+', ' ', traf_text).strip()
                all_content.append({'url': url, 'title': title, 'content': traf_clean, 'type': 'main_content'})
                content_added += 1
        except Exception:
            pass

        try:
            import html2text
            h2t = html2text.HTML2Text()
            h2t.ignore_links = True; h2t.ignore_images = True; h2t.body_width = 0
            md_text  = h2t.handle(html_text)
            md_clean = re.sub(r'\n{3,}', '\n\n', md_text).strip()
            md_clean = re.sub(r'[ \t]+', ' ', md_clean)
            if md_clean and len(md_clean) > 150:
                all_content.append({'url': url, 'title': title,
                                    'content': md_clean[:6000], 'type': 'html2text'})
                content_added += 1
        except Exception:
            pass

        structured = self._extract_structured_bs4(soup, url)
        valid = [p for p in structured if len(p.get('content', '')) > 30]
        for piece in valid:
            all_content.append(piece)
        if valid:
            content_added += len(valid)

        if content_added == 0:
            fallback = self._bs4_full_text(soup)
            if fallback and len(fallback) > 150:
                all_content.append({'url': url, 'title': title,
                                    'content': fallback[:8000], 'type': 'fallback'})

    def _extract_structured_bs4(self, soup, url):
        pieces = []
        for tag in soup(['script','style','nav','footer','header','aside','noscript','form']):
            tag.decompose()
        for tag in soup.find_all(True, {'class': re.compile(
                r'menu|nav|footer|header|sidebar|cookie|popup|modal|banner|social|share|comment', re.I)}):
            tag.decompose()

        for heading in soup.find_all(['h1','h2','h3','h4','h5']):
            heading_text = heading.get_text(separator=' ', strip=True)
            if not heading_text or len(heading_text) < 3 or len(heading_text) > 200:
                continue
            following_texts = []
            sibling = heading.find_next_sibling()
            depth = 0
            while sibling and depth < 8:
                if sibling.name in ('h1','h2','h3'):
                    break
                tag_text = re.sub(r'\s+', ' ', sibling.get_text(separator=' ', strip=True)).strip()
                if tag_text and len(tag_text) > 15:
                    following_texts.append(tag_text)
                depth += 1
                sibling = sibling.find_next_sibling()
            if following_texts:
                full_section = heading_text + ": " + " | ".join(following_texts)
                pieces.append({'url': url, 'content': re.sub(r'\s+', ' ', full_section).strip(), 'type': 'section'})
            else:
                pieces.append({'url': url, 'content': heading_text, 'type': 'heading'})

        for p in soup.find_all('p'):
            t = re.sub(r'\s+', ' ', p.get_text(separator=' ', strip=True)).strip()
            if len(t) > 40:
                pieces.append({'url': url, 'content': t, 'type': 'paragraph'})

        for ul in soup.find_all(['ul','ol']):
            items = [re.sub(r'\s+', ' ', li.get_text(separator=' ', strip=True)).strip()
                     for li in ul.find_all('li')]
            items = [i for i in items if i and len(i) > 3]
            if len(items) >= 2:
                pieces.append({'url': url, 'content': ', '.join(items), 'type': 'list'})

        card_pattern = re.compile(
            r'service|feature|card|item|box|tile|offer|package|benefit|solution|property|listing|why|about', re.I)
        for card in soup.find_all(True, {'class': card_pattern}):
            t = re.sub(r'\s+', ' ', card.get_text(separator=' ', strip=True)).strip()
            if 40 < len(t) < 3000:
                pieces.append({'url': url, 'content': t, 'type': 'card'})

        for table in soup.find_all('table'):
            rows = []
            for tr in table.find_all('tr'):
                cells = [td.get_text(strip=True) for td in tr.find_all(['td','th'])]
                row_text = ' | '.join(c for c in cells if c)
                if row_text and len(row_text) > 5:
                    rows.append(row_text)
            if rows:
                pieces.append({'url': url, 'content': '; '.join(rows), 'type': 'table'})

        seen, unique = set(), []
        for p in pieces:
            key = p['content'][:100]
            if key not in seen:
                seen.add(key); unique.append(p)
        return unique

    def _bs4_full_text(self, soup):
        for tag in soup(['script','style','nav','footer','aside','noscript']):
            tag.decompose()
        return re.sub(r'\s+', ' ', soup.get_text(separator=' ', strip=True)).strip()

    def chunk_website_content(self, content_items, website_url):
        chunks = []
        for item in content_items:
            text = item.get('content', '').strip()
            if not text or len(text) < 30:
                continue
            url   = item.get('url', website_url)
            title = item.get('title', '')
            ctype = item.get('type', 'general')

            if len(text.split()) <= 250:
                chunks.append({'text': text, 'url': url, 'title': title,
                                'type': ctype, 'source_type': 'website'})
                continue

            sentences = re.split(r'(?<=[.!?])\s+', text)
            current, cl = [], 0
            for sent in sentences:
                sent = sent.strip()
                if not sent:
                    continue
                wc = len(sent.split())
                if cl + wc <= 250:
                    current.append(sent); cl += wc
                else:
                    if current:
                        chunks.append({'text': ' '.join(current), 'url': url,
                                       'title': title, 'type': ctype, 'source_type': 'website'})
                    current, cl = [sent], wc
            if current:
                chunks.append({'text': ' '.join(current), 'url': url,
                               'title': title, 'type': ctype, 'source_type': 'website'})

        print(f"✅ Created {len(chunks)} chunks from website")
        return chunks

    # ─────────────────────────────────────────────
    # Vector Database
    # ─────────────────────────────────────────────

    def create_embeddings(self, chunks):
        """Use OpenAI text-embedding-3-small — fast, cheap, no local GPU needed."""
        print("🔧 Creating embeddings via OpenAI...")
        texts = [chunk['text'] for chunk in chunks]
        embeddings = []
        batch_size = 100  # OpenAI allows up to 2048 inputs per call
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i+batch_size]
            response = requests.post(
                'https://api.openai.com/v1/embeddings',
                headers={
                    'Authorization': f'Bearer {self.openai_api_key}',
                    'Content-Type': 'application/json'
                },
                json={'model': 'text-embedding-3-small', 'input': batch},
                timeout=60
            )
            if response.status_code != 200:
                raise Exception(f"Embedding API error: {response.status_code} {response.text}")
            data = response.json()
            batch_embeddings = [item['embedding'] for item in data['data']]
            embeddings.extend(batch_embeddings)
            done = min(i+batch_size, len(texts))
            print(f"  📊 {done}/{len(texts)} embeddings...")
        return embeddings

    def _embed_query(self, text):
        """Embed a single query string using OpenAI."""
        response = requests.post(
            'https://api.openai.com/v1/embeddings',
            headers={
                'Authorization': f'Bearer {self.openai_api_key}',
                'Content-Type': 'application/json'
            },
            json={'model': 'text-embedding-3-small', 'input': text},
            timeout=30
        )
        if response.status_code != 200:
            raise Exception(f"Embedding API error: {response.status_code}")
        return response.json()['data'][0]['embedding']

    def add_to_vector_database(self, chunks, source_name, source_type='pdf'):
        print(f"🔧 Adding {len(chunks)} chunks to vector database...")
        try:
            collection = self.chroma_client.get_collection(self.all_docs_collection)
            print("📚 Using existing main collection")
        except Exception:
            collection = self.chroma_client.create_collection(
                name=self.all_docs_collection, embedding_function=None)
            print("📚 Created new main collection")

        embeddings = self.create_embeddings(chunks)
        doc_ids, documents, metadatas = [], [], []
        for i, chunk in enumerate(chunks):
            content_hash = hashlib.md5(chunk['text'].encode()).hexdigest()[:10]
            doc_id = f"{source_type}_{hashlib.md5(source_name.encode()).hexdigest()[:8]}_{i}_{content_hash}"
            doc_ids.append(doc_id)
            documents.append(chunk['text'])
            meta = {"source": source_name, "type": chunk.get('type','general'),
                    "chunk_index": i, "source_type": source_type}
            if source_type == 'pdf':
                meta['page'] = chunk.get('page', 1)
            else:
                meta['url']   = chunk.get('url', source_name)
                meta['title'] = chunk.get('title', '')
            metadatas.append(meta)

        BATCH = 100
        for start in range(0, len(doc_ids), BATCH):
            collection.add(
                documents=documents[start:start+BATCH],
                embeddings=embeddings[start:start+BATCH],
                ids=doc_ids[start:start+BATCH],
                metadatas=metadatas[start:start+BATCH]
            )
        print(f"✅ Added {len(chunks)} chunks | Total in DB: {collection.count()}")
        return collection

    # ─────────────────────────────────────────────
    # Query & Answer Generation
    # ─────────────────────────────────────────────

    def query_all_sources(self, question, n_results=20):
        try:
            collection = self.chroma_client.get_collection(self.all_docs_collection)
        except Exception as e:
            print(f"❌ Collection not found: {e}")
            return None

        count = collection.count()
        if count == 0:
            print("❌ Database is empty.")
            return None

        safe_n = max(1, min(n_results, count))
        question_embedding = self._embed_query(question)
        results = collection.query(
            query_embeddings=[question_embedding],
            n_results=safe_n,
            include=["documents","metadatas","distances"]
        )

        if (results and results.get("documents") and results["documents"][0]
                and results.get("metadatas") and results["metadatas"][0]):
            combined = list(zip(results["documents"][0],
                                results["metadatas"][0],
                                results["distances"][0]))

            def sort_key(item):
                _, meta, dist = item
                src = str(meta.get("source","") or meta.get("source_type","")).lower()
                return (1 if ("pdf" in src or ".pdf" in src) else 2, dist)

            combined.sort(key=sort_key)
            results["documents"][0] = [x[0] for x in combined]
            results["metadatas"][0] = [x[1] for x in combined]
            results["distances"][0] = [x[2] for x in combined]
            pdf_count = sum(1 for _,m,_ in combined if "pdf" in str(m.get("source","")).lower())
            print(f"  INFO: {len(combined)} chunks ({pdf_count} PDF, {len(combined)-pdf_count} web)")

        return results

    def _extract_person_info(self, name, combined_info):
        info = {}
        name_idx = combined_info.lower().find(name.lower())
        if name_idx == -1:
            return info
        window = combined_info[max(0, name_idx-50): name_idx+600]
        patterns = {
            'role':        r'Role\s*:\s*(.+?)(?=\s*(?:Department|Contact|Phone|Mobile|Email|Salary|Nationality|Visa|Joined|$))',
            'department':  r'Department\s*:\s*(.+?)(?=\s*(?:Role|Contact|Phone|Mobile|Email|Salary|Nationality|Visa|Joined|$))',
            'phone':       r'(?:Contact Number|Phone|Mobile|Tel)\s*:\s*(\+?[\d\s\-]{7,20})',
            'email':       r'[Ee]mail\s*:\s*([\w._%+\-]+@[\w.\-]+\.[a-zA-Z]{2,})',
            'salary':      r'Salary\s*:\s*(.+?)(?=\s*(?:Role|Department|Contact|Email|Nationality|Visa|$))',
            'nationality': r'Nationality\s*:\s*(.+?)(?=\s*(?:Role|Department|Contact|Email|Visa|Joined|$))',
            'visa':        r'Visa\s*:\s*(.+?)(?=\s*(?:Role|Department|Contact|Email|Nationality|Joined|$))',
            'joined':      r'(?:Joined|Start Date|Joining Date)\s*:\s*(.+?)(?=\s*(?:Role|Department|Contact|Email|$))',
        }
        for field, pattern in patterns.items():
            match = re.search(pattern, window, re.IGNORECASE)
            if match:
                info[field] = match.group(1).strip()
        email_raw = re.search(
            r'([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+(?:[•\s]+)?[a-zA-Z]{2,})', window)
        if email_raw:
            info['email'] = email_raw.group(1).replace('• ', '.').replace('•', '.').strip()
        return info

    def _format_person_response(self, name, info, question, combined_info="", openai_fn=None):
        q = question.lower()
        known = []
        if info.get('role'):        known.append(f"Role: {info['role']}")
        if info.get('department'):  known.append(f"Department: {info['department']}")
        if info.get('phone'):       known.append(f"Phone: {info['phone']}")
        if info.get('email'):       known.append(f"Email: {info['email']}")
        if info.get('nationality'): known.append(f"Nationality: {info['nationality']}")
        if info.get('visa'):        known.append(f"Visa Status: {info['visa']}")
        if info.get('salary'):      known.append(f"Salary: {info['salary']}")
        if info.get('joined'):      known.append(f"Joined: {info['joined']}")

        if openai_fn and known:
            person_data = "\n".join(known)
            prompt = (
                f"Write a natural, conversational 2-3 sentence introduction about {name} "
                f"using ONLY the following data. Include their role, responsibilities, "
                f"and contact details. No bullet points or markdown.\n\n"
                f"Data:\n{person_data}\n\n"
                f"Additional context:\n{combined_info[:1500]}"
            )
            try:
                answer = openai_fn(question, prompt, "")
                if answer and len(answer) > 20:
                    return answer
            except Exception:
                pass

        if any(w in q for w in ['number','phone','mobile','call','reach','contact','whatsapp']):
            phone = info.get('phone',''); email = info.get('email',''); role = info.get('role','')
            base  = f"{name}" + (f", {role}," if role else "")
            if phone and email: return f"{base} can be reached at {phone} or by email at {email}."
            if phone:           return f"You can reach {name} at {phone}."
            if email:           return f"The best way to reach {name} is by email at {email}."
            return f"I don't have direct contact details for {name} in our records."

        if 'email' in q or 'mail' in q:
            email = info.get('email','')
            return f"{name}'s email address is {email}." if email else f"I don't have an email for {name}."

        if any(w in q for w in ['salary','pay','compensation']):
            salary = info.get('salary','')
            return f"{name}'s salary is {salary}." if salary else f"I don't have salary details for {name}."

        if known:
            role = info.get('role',''); dept = info.get('department','')
            phone = info.get('phone',''); email = info.get('email','')
            intro = f"{name} is the {role}" if role else name
            if dept: intro += f" in the {dept} department"
            intro += "."
            contacts = []
            if phone: contacts.append(f"phone: {phone}")
            if email: contacts.append(f"email: {email}")
            if contacts: intro += f" You can contact them via {' or '.join(contacts)}."
            return intro
        return f"I found {name} in our records but couldn't extract the details you're looking for."

    NON_PERSON_WORDS = {
        'real estate','your place','operations manager','sales manager',
        'leasing manager','property manager','finance manager','hr manager',
        'general manager','managing director','chief executive','senior consultant',
        'property consultant','leasing executive','finance executive',
        'accounts executive','residential sales','commercial sales',
        'property management','united arab','arab emirates','dubai marina',
        'business bay','downtown dubai','jumeirah village','dubai hills',
        'monday friday','sunday thursday',
    }

    def _is_person_name(self, candidate):
        lower = candidate.lower()
        if lower in self.NON_PERSON_WORDS:
            return False
        role_words = {
            'manager','director','executive','consultant','officer','coordinator',
            'assistant','associate','analyst','agent','place','real','estate',
            'dubai','marina','bay','village','hills','emirates','arab','united',
            'monday','tuesday','wednesday','thursday','friday','saturday','sunday',
        }
        if any(p in role_words for p in candidate.lower().split()):
            return False
        return True

    def _resolve_target_name(self, question, conversation_context, combined_info):
        direct = re.findall(r'\b([A-Z][a-z]+ [A-Z][a-z]+)\b', question)
        for name in direct:
            if self._is_person_name(name):
                return name
        q = question.lower()
        has_pronoun = bool(re.search(r'\b(her|his|their|she|he|them)\b', q))
        if has_pronoun and conversation_context:
            all_names    = re.findall(r'\b([A-Z][a-z]+ [A-Z][a-z]+)\b', conversation_context)
            person_names = [n for n in all_names if self._is_person_name(n)]
            if person_names:
                return person_names[-1]
        return None

    def _is_contact_query(self, question):
        contact_words = ['phone','mobile','cell','whatsapp','email','e-mail',
                         'salary','pay','compensation','nationality','passport',
                         'visa','employee id','joining date','start date']
        q = question.lower()
        has_contact_word = any(re.search(r'\b' + re.escape(w) + r'\b', q) for w in contact_words)
        has_pronoun      = bool(re.search(r'\b(her|his|their|she|he|them)\b', q))
        return has_contact_word or has_pronoun

    def generate_conversational_answer(self, question, context_chunks, conversation_context=""):
        if not context_chunks or not context_chunks.get('documents') or not context_chunks['documents'][0]:
            return "I couldn't find any information about that in our documents or website."

        # ── Step 1: keyword search (raw PDF first — most reliable) ────────────
        keyword_hits = self.keyword_search(question)

        # ── Step 2: embedding results (PDF-reranked) ──────────────────────────
        embedding_docs = context_chunks['documents'][0][:20]

        # ── Step 3: merge — keyword hits first, deduplicated ─────────────────
        CHAR_LIMIT = 14000
        seen_keys, relevant_info, total_chars = set(), [], 0

        for doc in list(keyword_hits) + list(embedding_docs):
            clean = re.sub(r'\s+', ' ', doc).strip()
            key   = clean[:60]
            if key in seen_keys:
                continue
            seen_keys.add(key)
            if total_chars + len(clean) <= CHAR_LIMIT:
                relevant_info.append(clean)
                total_chars += len(clean)
            else:
                remaining = CHAR_LIMIT - total_chars
                if remaining > 200:
                    relevant_info.append(clean[:remaining])
                break

        combined_info = '\n\n'.join(relevant_info)
        print(f"  INFO: Context = {len(combined_info)} chars from {len(relevant_info)} chunks "
              f"({len(keyword_hits)} keyword + {len(list(embedding_docs))} embedding)")

        # ── Contact/personal: try direct extraction first ─────────────────────
        if self._is_contact_query(question):
            target_name = self._resolve_target_name(question, conversation_context, combined_info)
            print(f"  INFO: Contact query. Target: {target_name}")
            if target_name:
                person_info = self._extract_person_info(target_name, combined_info)
                if person_info:
                    return self._format_person_response(
                        target_name, person_info, question,
                        combined_info=combined_info,
                        openai_fn=self._answer_with_openai
                    )
                print(f"  INFO: No structured data for {target_name}, falling to OpenAI")
            else:
                print(f"  INFO: No target name resolved, falling to OpenAI")

        return self._answer_with_openai(question, combined_info, conversation_context)

    # ── Hardcoded company facts that may not appear in documents ─────────────
    COMPANY_FACTS = (
        "COMPANY QUICK REFERENCE (always use these exact values):\n"
        "CEO of both companies: Marcello\n"
        "Your Place Real Estate website: https://yourplace.ae\n"
        "MMP (Manage My Property) website: https://managemyproperty.ae\n"
        "Your Place Real Estate: real estate agency — buying, selling, leasing properties\n"
        "MMP (Manage My Property): property management — tenant relations, rent collection, maintenance\n"
        "Both companies are operated together under the same leadership.\n"
        "Location: Business Bay, Dubai, UAE\n"
        "Note: For staff contact numbers, use the exact number from the staff record in the knowledge base.\n"
    )

    def _answer_with_openai(self, question, combined_info, conversation_context=""):
        system_prompt = (
            "You are a helpful assistant for Your Place Real Estate and MMP (Manage My Property). "
            "Answer every question using the COMPANY KNOWLEDGE BASE and COMPANY QUICK REFERENCE provided.\n\n"
            "HOW TO FIND STAFF: Staff records follow this format — "
            "[Person Name] Role: [title] Department: [dept] Contact Number: [phone] Email: [email] "
            "Services/Responsibilities: [list]. "
            "To answer 'who is the [role]', find the record whose Role field matches that title, "
            "then introduce that person by name.\n\n"
            "RULES:\n"
            "1. PLAIN TEXT ONLY — no asterisks, no bullet symbols, no bold, no # headings.\n"
            "2. STAFF: Give full name, role, 1-2 sentence description of responsibilities, "
            "then phone and email.\n"
            "3. TIMINGS/LOCATION: State the exact value from the document.\n"
            "4. WEBSITE/CONTACT: Use the COMPANY QUICK REFERENCE values.\n"
            "5. COMPLETE — never stop mid-sentence.\n"
            "6. Never say 'based on the context' or 'according to the documents'.\n"
            "7. Only say info is unavailable if genuinely absent after reading everything.\n"
            "8. Never invent data."
        )

        messages = [{"role": "system", "content": system_prompt}]

        if conversation_context and conversation_context.strip():
            for line in conversation_context.strip().split("\n"):
                line = line.strip()
                if line.startswith("User:"):
                    messages.append({"role": "user", "content": line[5:].strip()})
                elif line.startswith("Assistant:"):
                    messages.append({"role": "assistant", "content": line[10:].strip()})

        messages.append({
            "role": "system",
            "content": (self.COMPANY_FACTS + "\n\nCOMPANY KNOWLEDGE BASE — absolute source of truth:\n\n" + combined_info)
        })
        messages.append({"role": "user", "content": question})

        print(f"  INFO: OpenAI {self.openai_model} | {len(combined_info)} chars | {len(messages)} msgs")

        try:
            response = requests.post(
                self.openai_url,
                headers={"Authorization": f"Bearer {self.openai_api_key}",
                         "Content-Type": "application/json"},
                json={"model": self.openai_model, "messages": messages,
                      "max_tokens": 2500, "temperature": 0.3},
                timeout=60
            )
            if response.status_code == 200:
                answer = response.json()["choices"][0]["message"]["content"].strip()
                print(f"  OK: OpenAI answered ({len(answer)} chars)")
                answer = re.sub(r'\*\*(.+?)\*\*', r'\1', answer)
                answer = re.sub(r'\*(.+?)\*',     r'\1', answer)
                answer = re.sub(r'^#{1,4}\s+',     '',   answer, flags=re.MULTILINE)
                answer = re.sub(r'^\*\s+',         '',   answer, flags=re.MULTILINE)
                answer = re.sub(r'^-\s+',          '',   answer, flags=re.MULTILINE)
                answer = re.sub(r'`(.+?)`',        r'\1', answer)
                return answer.strip()
            elif response.status_code == 401:
                return "The AI service is not configured correctly. Please contact the site administrator."
            elif response.status_code == 429:
                return "I'm receiving a lot of requests right now. Please try again in a few seconds."
            else:
                err = response.json().get("error", {}).get("message", "Unknown error")
                print(f"  ERROR: OpenAI HTTP {response.status_code}: {err}")
                return self._fallback_answer(question, combined_info)
        except requests.exceptions.Timeout:
            return "The AI service took too long to respond. Please try again."
        except Exception as e:
            print(f"  ERROR: OpenAI call failed: {e}")
            return self._fallback_answer(question, combined_info)

    def _fallback_answer(self, question, info):
        q = question.lower()
        if any(w in q for w in ['service','services','provide','offer']):
            sentences = [s.strip() for s in re.split(r'[.;]', info) if len(s.strip()) > 20]
            if sentences:
                return "We offer: " + "; ".join(sentences[:6]) + "."
        snippet = info[:400].strip()
        if snippet:
            return f"Here's what I found: {snippet}... For more details please contact our team."
        return "I'm sorry, I don't have specific information on that right now. Please contact our team."

    # ─────────────────────────────────────────────
    # High-level entry points
    # ─────────────────────────────────────────────

    def process_pdf(self, pdf_path):
        print("\n" + "=" * 60)
        print("🚀 Processing PDF Document")
        print("=" * 60)
        pdf_name = os.path.basename(pdf_path)
        print(f"📄 Processing: {pdf_name}")
        text, pages_text = self.extract_text_from_pdf(pdf_path)
        if not text:
            return False
        self._save_raw_pdf_text(pdf_name, text)   # ← cache for keyword search
        chunks = self.smart_chunk_text(text, pages_text, pdf_name)
        self.add_to_vector_database(chunks, pdf_name, 'pdf')
        print("\n" + "=" * 60)
        print("✅ PDF Processing Complete!")
        print("=" * 60)
        return True

    def process_website(self, url, max_pages=10):
        print("\n" + "=" * 60)
        print("🌐 Processing Website")
        print("=" * 60)
        print(f"URL: {url}\nMax Pages: {max_pages}")
        try:
            content = self.extract_website_content(url, max_pages)
            if not content:
                return False, "No content extracted from website"
            chunks = self.chunk_website_content(content, url)
            if not chunks:
                return False, "No chunks created from website content"
            self.add_to_vector_database(chunks, url, 'website')
            metadata = {
                'website_url': url,
                'collection_name': f"website_{hashlib.md5(url.encode()).hexdigest()[:12]}",
                'pages_processed': len(set(c['url'] for c in content)),
                'chunk_count': len(chunks),
                'processed_date': time.strftime('%Y-%m-%d %H:%M:%S')
            }
            meta_file = os.path.join(self.db_path,
                f"web_{hashlib.md5(url.encode()).hexdigest()[:12]}_metadata.json")
            with open(meta_file, 'w') as f:
                json.dump(metadata, f, indent=2)
            print("\n" + "=" * 60)
            print("✅ Website Processing Complete!")
            print(f"📊 Pages: {metadata['pages_processed']} | Chunks: {len(chunks)}")
            print("=" * 60)
            return True, f"Successfully processed {metadata['pages_processed']} pages"
        except Exception as e:
            import traceback; traceback.print_exc()
            return False, str(e)

    def clear_database(self):
        try:
            self.chroma_client.delete_collection(self.all_docs_collection)
            print("✅ All data cleared")
        except Exception:
            print("No database found to clear")

    def get_stats(self):
        try:
            collection = self.chroma_client.get_collection(self.all_docs_collection)
            count = collection.count()
            all_data = collection.get()
            sources = {}
            if all_data and 'metadatas' in all_data:
                for meta in all_data['metadatas']:
                    st  = meta.get('source_type', 'unknown')
                    src = meta.get('source', 'Unknown')
                    key = f"{st}: {src}"
                    sources[key] = sources.get(key, 0) + 1
            return {'total_chunks': count, 'sources': sources}
        except Exception:
            return None

    def interactive_query(self):
        print("\n" + "=" * 60)
        print("💬 Conversational Query Mode")
        print("=" * 60)
        stats = self.get_stats()
        if stats:
            print(f"📚 Database has {stats['total_chunks']} chunks ready\n")
        else:
            print("❌ No documents found.\n")
            return
        while True:
            question = input("\n❓ You: ").strip()
            if question.lower() == 'exit':
                print("\n👋 Goodbye!")
                break
            if not question:
                continue
            print("🤔 Thinking...")
            results = self.query_all_sources(question)
            if results and results['documents'] and results['documents'][0]:
                answer = self.generate_conversational_answer(question, results)
                print(f"\n💬 Assistant: {answer}\n")
            else:
                print("\n💬 Assistant: I couldn't find relevant information for that question.\n")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Enhanced RAG Processor")
    parser.add_argument("--pdf",         help="Path to PDF file to process")
    parser.add_argument("--website",     help="Website URL to process")
    parser.add_argument("--max-pages",   type=int, default=10)
    parser.add_argument("--query",       help="Ask a question")
    parser.add_argument("--context",     help="Previous conversation context", default="")
    parser.add_argument("--interactive", action="store_true")
    parser.add_argument("--check",       action="store_true")
    parser.add_argument("--warmup",      action="store_true")
    parser.add_argument("--stats",       action="store_true")
    parser.add_argument("--clear",       action="store_true")
    parser.add_argument("--list",        action="store_true")
    parser.add_argument("--remove",      help="Remove a source by name")
    parser.add_argument("--openai-key",  help="OpenAI API key", default="")
    args = parser.parse_args()

    openai_key = getattr(args, 'openai_key', '') or os.environ.get('OPENAI_API_KEY', '')
    processor  = RAGProcessor(openai_api_key=openai_key if openai_key else None)

    if args.clear:
        processor.clear_database(); return
    if args.warmup:
        return
    if args.check:
        print("\n" + "=" * 60)
        print("🔍 System Status Check")
        print("=" * 60)
        stats = processor.get_stats()
        if stats:
            print(f"✅ Database: {stats['total_chunks']} chunks")
            for src, cnt in stats['sources'].items():
                print(f"     • {src}: {cnt} chunks")
        else:
            print("❌ Database: empty")
        return
    if args.stats or args.list:
        stats = processor.get_stats()
        if stats:
            print(f"\n📊 Total chunks: {stats['total_chunks']}")
            for src, cnt in stats['sources'].items():
                print(f"  • {src}: {cnt} chunks")
        else:
            print("❌ No database found")
        return
    if args.pdf:
        processor.process_pdf(args.pdf)
    if args.website:
        success, message = processor.process_website(args.website, args.max_pages)
        print(f"\n{'✅' if success else '❌'} {message}")
    if args.interactive:
        processor.interactive_query()
    if args.query and not args.interactive:
        stats = processor.get_stats()
        if not stats:
            print("❌ No documents found.")
            return
        print(f"\n🔍 Query: {args.query}")
        print("-" * 60)
        results = processor.query_all_sources(args.query)
        if results and results['documents'] and results['documents'][0]:
            answer = processor.generate_conversational_answer(
                args.query, results, getattr(args, "context", ""))
            print(f"\n<<<ANSWER_START>>>\n{answer}\n<<<ANSWER_END>>>")
        else:
            print("No relevant information found.")

if __name__ == "__main__":
    main()
