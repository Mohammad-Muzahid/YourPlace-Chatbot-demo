"""
Flask API wrapper for the RAG Processor.
Uses /tmp for storage (compatible with Render free tier).
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import sys
import traceback
import tempfile

sys.path.insert(0, os.path.dirname(__file__))
from rag_processor import RAGProcessor

app = Flask(__name__)
CORS(app)

OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY', '')
ADMIN_KEY      = os.environ.get('ADMIN_KEY', 'change-this-secret')

# Use /tmp which is always writable on Render free tier
# NOTE: /tmp is ephemeral — data resets on restart.
# After each restart you need to re-upload PDFs.
# Upgrade to paid plan ($7/mo) to get persistent disk.
DB_PATH = os.environ.get('DB_PATH', '/tmp/vector_db')
os.makedirs(DB_PATH, exist_ok=True)

processor = None

def get_processor():
    global processor
    if processor is None:
        print("Loading RAG processor...")
        processor = RAGProcessor(
            db_path=DB_PATH,
            openai_api_key=OPENAI_API_KEY
        )
        print("RAG processor ready.")
    return processor


@app.route('/', methods=['GET'])
def index():
    return jsonify({
        'service': 'YourPlace Chatbot API',
        'status': 'running',
        'endpoints': ['/health', '/query', '/process-pdf', '/process-website', '/stats']
    })


@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'message': 'Chatbot API is running'})


@app.route('/query', methods=['POST'])
def query():
    try:
        data     = request.get_json(force=True) or {}
        question = data.get('question', '').strip()
        context  = data.get('context', '')

        if not question:
            return jsonify({'error': 'No question provided'}), 400

        proc    = get_processor()
        results = proc.query_all_sources(question)

        if not results or not results.get('documents') or not results['documents'][0]:
            return jsonify({'answer': "I couldn't find relevant information for that question."})

        answer = proc.generate_conversational_answer(question, results, context)
        return jsonify({'answer': answer})

    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e), 'answer': 'Something went wrong. Please try again.'}), 500


@app.route('/process-pdf', methods=['POST'])
def process_pdf():
    if request.headers.get('X-Admin-Key') != ADMIN_KEY:
        return jsonify({'error': 'Unauthorized'}), 401

    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400

    file = request.files['file']
    if not file.filename.endswith('.pdf'):
        return jsonify({'error': 'Only PDF files accepted'}), 400

    with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False, dir='/tmp') as tmp:
        file.save(tmp.name)
        tmp_path = tmp.name

    try:
        proc    = get_processor()
        success = proc.process_pdf(tmp_path)
        os.unlink(tmp_path)
        if success:
            return jsonify({'status': 'ok', 'message': f'{file.filename} processed successfully'})
        else:
            return jsonify({'error': 'PDF processing failed'}), 500
    except Exception as e:
        traceback.print_exc()
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        return jsonify({'error': str(e)}), 500


@app.route('/process-website', methods=['POST'])
def process_website():
    if request.headers.get('X-Admin-Key') != ADMIN_KEY:
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.get_json(force=True) or {}
    url  = data.get('url', '').strip()
    if not url:
        return jsonify({'error': 'No URL provided'}), 400

    try:
        proc = get_processor()
        success, message = proc.process_website(url, max_pages=data.get('max_pages', 20))
        return jsonify({'status': 'ok' if success else 'error', 'message': message})
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/stats', methods=['GET'])
def stats():
    if request.headers.get('X-Admin-Key') != ADMIN_KEY:
        return jsonify({'error': 'Unauthorized'}), 401
    try:
        proc  = get_processor()
        stats = proc.get_stats()
        return jsonify(stats or {'total_chunks': 0, 'sources': {}})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/clear', methods=['POST'])
def clear():
    if request.headers.get('X-Admin-Key') != ADMIN_KEY:
        return jsonify({'error': 'Unauthorized'}), 401
    try:
        proc = get_processor()
        proc.clear_database()
        return jsonify({'status': 'ok', 'message': 'Database cleared'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"Starting chatbot API on port {port}...")
    app.run(host='0.0.0.0', port=port, debug=False)
