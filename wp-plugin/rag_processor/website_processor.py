#!/usr/bin/env python3
"""
Enhanced Website Processor for RAG Chatbot
Better extraction and analysis of website content
"""

import os
import json
import requests
import time
import re
import hashlib
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup
import trafilatura
from sentence_transformers import SentenceTransformer
import chromadb

class WebsiteProcessor:
    def __init__(self, db_path=None):
        """
        Initialize the Website Processor
        """
        self.db_path = db_path or os.path.join(os.path.dirname(__file__), 'vector_db')
        self.ollama_url = "http://localhost:11434"
        self.model_name = "llama2"
        
        # Create db directory if it doesn't exist
        os.makedirs(self.db_path, exist_ok=True)
        
        # Initialize ChromaDB client
        self.chroma_client = chromadb.PersistentClient(path=self.db_path)
        
        # Use local embeddings
        self.embedding_model = SentenceTransformer('all-MiniLM-L6-v2')
        
        # Main collection for all documents
        self.all_docs_collection = "all_company_documents"
        
        # Model warming flag
        self.model_warmed = False
        
    def warm_up_model(self):
        """Pre-load model to avoid first-query delay"""
        if not self.model_warmed:
            print("  🔥 Warming up Llama 2 model...")
            try:
                requests.post(
                    f"{self.ollama_url}/api/generate",
                    json={
                        "model": self.model_name,
                        "prompt": "Hello",
                        "stream": False,
                        "options": {
                            "num_predict": 5,
                            "temperature": 0.1
                        }
                    },
                    timeout=30
                )
                self.model_warmed = True
                print("  ✅ Model warmed up!")
            except Exception as e:
                print(f"  ⚠️ Warm-up failed: {e}")
    
    def extract_website_content(self, url, max_pages=10):
        """
        Enhanced website content extraction
        """
        print(f"🌐 Extracting content from: {url}")
        
        visited_urls = set()
        to_visit = [url]
        all_content = []
        base_domain = urlparse(url).netloc
        
        while to_visit and len(visited_urls) < max_pages:
            current_url = to_visit.pop(0)
            if current_url in visited_urls:
                continue
            
            print(f"  📄 Scraping: {current_url}")
            
            try:
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
                }
                response = requests.get(current_url, headers=headers, timeout=10)
                
                if response.status_code == 200:
                    # Extract main content with trafilatura
                    extracted_text = trafilatura.extract(response.text)
                    
                    if extracted_text:
                        # Clean the text
                        extracted_text = re.sub(r'\s+', ' ', extracted_text).strip()
                        
                        if len(extracted_text) > 100:  # Only keep substantial content
                            all_content.append({
                                'url': current_url,
                                'content': extracted_text,
                                'type': 'main'
                            })
                            
                            # Also extract lists and structured content
                            self._extract_structured_content(response.text, current_url, all_content)
                    
                    visited_urls.add(current_url)
                    time.sleep(0.5)  # Be polite
                    
                    # Find more links
                    soup = BeautifulSoup(response.text, 'html.parser')
                    for link in soup.find_all('a', href=True)[:5]:
                        href = link['href']
                        full_url = urljoin(current_url, href)
                        
                        if urlparse(full_url).netloc == base_domain:
                            if full_url not in visited_urls and full_url not in to_visit:
                                if not any(skip in full_url.lower() for skip in ['#', 'mailto:', 'tel:', '.pdf', '.jpg', '.png', 'login', 'signup']):
                                    to_visit.append(full_url)
                    
            except Exception as e:
                print(f"    ⚠️ Error: {e}")
                visited_urls.add(current_url)
        
        print(f"✅ Extracted {len(all_content)} content items from {len(visited_urls)} pages")
        return all_content
    
    def _extract_structured_content(self, html, url, all_content):
        """Extract lists, services, and structured information"""
        soup = BeautifulSoup(html, 'html.parser')
        
        # Extract lists (often contain services, features)
        for ul in soup.find_all('ul'):
            items = []
            for li in ul.find_all('li'):
                text = li.get_text().strip()
                if text and len(text) > 10:
                    items.append(text)
            
            if items:
                all_content.append({
                    'url': url,
                    'content': '• ' + '\n• '.join(items),
                    'type': 'list'
                })
        
        # Extract headings with following content (sections)
        for heading in soup.find_all(['h1', 'h2', 'h3']):
            heading_text = heading.get_text().strip()
            if heading_text and len(heading_text) > 5:
                # Get next few paragraphs
                next_content = []
                next_elem = heading.find_next_sibling()
                count = 0
                while next_elem and count < 3:
                    if next_elem.name in ['p', 'div']:
                        text = next_elem.get_text().strip()
                        if text and len(text) > 20:
                            next_content.append(text)
                    count += 1
                    next_elem = next_elem.find_next_sibling()
                
                if next_content:
                    section_text = f"{heading_text}\n" + "\n".join(next_content)
                    all_content.append({
                        'url': url,
                        'content': section_text,
                        'type': 'section'
                    })
    
    def chunk_content(self, content_items, website_url):
        """Smart chunking for website content"""
        chunks = []
        
        for item in content_items:
            text = item['content']
            url = item['url']
            content_type = item.get('type', 'general')
            
            # Split into logical chunks
            if content_type == 'list':
                # Keep lists together
                chunks.append({
                    'text': text,
                    'url': url,
                    'type': 'list',
                    'source': website_url
                })
            else:
                # Split by sentences for longer content
                sentences = re.split(r'(?<=[.!?])\s+', text)
                current_chunk = []
                current_length = 0
                
                for sentence in sentences:
                    words = len(sentence.split())
                    if current_length + words < 200:
                        current_chunk.append(sentence)
                        current_length += words
                    else:
                        if current_chunk:
                            chunks.append({
                                'text': ' '.join(current_chunk),
                                'url': url,
                                'type': content_type,
                                'source': website_url
                            })
                        current_chunk = [sentence]
                        current_length = words
                
                if current_chunk:
                    chunks.append({
                        'text': ' '.join(current_chunk),
                        'url': url,
                        'type': content_type,
                        'source': website_url
                    })
        
        print(f"✅ Created {len(chunks)} chunks from website")
        return chunks
    
    def create_embeddings(self, chunks):
        """Create embeddings for chunks"""
        print("🔧 Creating embeddings...")
        embeddings = []
        texts = [chunk['text'] for chunk in chunks]
        
        batch_size = 10
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i+batch_size]
            batch_embeddings = self.embedding_model.encode(batch).tolist()
            embeddings.extend(batch_embeddings)
            print(f"  📊 Processed {min(i+batch_size, len(texts))}/{len(chunks)} embeddings...")
        
        return embeddings
    
    def create_vector_database(self, chunks, website_url):
        """Add website content to main collection"""
        print(f"🔧 Adding website content to database...")
        
        # Get or create main collection
        try:
            collection = self.chroma_client.get_collection(self.all_docs_collection)
            print("📚 Using existing main collection")
        except:
            collection = self.chroma_client.create_collection(
                name=self.all_docs_collection,
                embedding_function=None
            )
            print("📚 Created new main collection")
        
        # Create embeddings
        embeddings = self.create_embeddings(chunks)
        
        # Prepare data
        doc_ids = []
        documents = []
        metadatas = []
        
        for i, chunk in enumerate(chunks):
            content_hash = hashlib.md5(chunk['text'].encode()).hexdigest()[:10]
            doc_id = f"web_{hashlib.md5(website_url.encode()).hexdigest()[:8]}_{i}_{content_hash}"
            
            doc_ids.append(doc_id)
            documents.append(chunk['text'])
            metadatas.append({
                "source": website_url,
                "url": chunk['url'],
                "type": chunk.get('type', 'general'),
                "chunk_index": i,
                "document_type": "website"
            })
        
        # Add to collection
        collection.add(
            documents=documents,
            embeddings=embeddings,
            ids=doc_ids,
            metadatas=metadatas
        )
        
        print(f"✅ Added {len(chunks)} chunks from website")
        print(f"📊 Total chunks in collection: {collection.count()}")
        
        return collection
    
    def process_website(self, url, max_pages=10):
        """Complete website processing pipeline"""
        print("\n" + "="*60)
        print("🌐 Enhanced Website Processing")
        print("="*60)
        
        # Extract content
        content = self.extract_website_content(url, max_pages)
        if not content:
            print("❌ No content extracted")
            return False
        
        # Chunk content
        chunks = self.chunk_content(content, url)
        
        # Add to database
        collection = self.create_vector_database(chunks, url)
        
        print("\n" + "="*60)
        print("✅ Website Processing Complete!")
        print(f"🌐 URL: {url}")
        print(f"📊 Chunks: {len(chunks)}")
        print(f"📚 Total in database: {collection.count()}")
        print("="*60)
        
        return True
    
    def check_ollama_status(self):
        """Check if Ollama is running"""
        try:
            response = requests.get(f"{self.ollama_url}/api/tags", timeout=5)
            if response.status_code == 200:
                return {"running": True, "models": response.json().get("models", [])}
            return {"running": False}
        except:
            return {"running": False}

def main():
    """Command-line interface"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Enhanced Website Processor")
    parser.add_argument("--url", help="Website URL to process")
    parser.add_argument("--max-pages", type=int, default=10, help="Maximum pages to scrape")
    parser.add_argument("--check", action="store_true", help="Check system status")
    parser.add_argument("--warmup", action="store_true", help="Warm up the model")
    
    args = parser.parse_args()
    
    processor = WebsiteProcessor()
    
    # Warm up model
    if args.warmup:
        processor.warm_up_model()
        return
    
    # Check status
    if args.check:
        print("\n" + "="*60)
        print("🔍 System Status Check")
        print("="*60)
        
        # Check Ollama
        status = processor.check_ollama_status()
        if status["running"]:
            print(f"✅ Ollama: Running")
            if status["models"]:
                print(f"   Models: {', '.join([m['name'] for m in status['models']])}")
        else:
            print(f"❌ Ollama: Not running")
        
        return
    
    # Process website
    if args.url:
        processor.process_website(args.url, args.max_pages)

if __name__ == "__main__":
    main()