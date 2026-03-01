#!/usr/bin/env python3
"""
Warm up Llama 2 model to prevent timeouts
"""

import requests
import time

print("🔥 Warming up Llama 2 model...")
print("This may take 30-60 seconds on first run\n")

try:
    start_time = time.time()
    
    response = requests.post(
        "http://localhost:11434/api/generate",
        json={
            "model": "llama2",
            "prompt": "Hello, please respond with a short greeting.",
            "stream": False,
            "options": {
                "temperature": 0.3,
                "num_predict": 20
            }
        },
        timeout=120
    )
    
    if response.status_code == 200:
        result = response.json()
        elapsed = time.time() - start_time
        print(f"✅ Model warmed up successfully in {elapsed:.1f} seconds!")
        print(f"   Response: {result.get('response', '').strip()}")
    else:
        print(f"❌ Failed to warm up model: HTTP {response.status_code}")
        
except requests.exceptions.Timeout:
    print("❌ Timeout - model might still be loading. Try running 'ollama run llama2' in terminal first.")
except Exception as e:
    print(f"❌ Error: {e}")

print("\nYou can now use the RAG chatbot with faster responses!")