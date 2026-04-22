"""Detailed Kimi API key diagnostic."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

key = os.environ.get("MOONSHOT_API_KEY", "")
print(f"Key length: {len(key)}")
print(f"Key starts with: {key[:15] if key else 'EMPTY'}")
print(f"Key ends with: {key[-10:] if key else 'EMPTY'}")
print(f"Contains whitespace: {any(c.isspace() for c in key)}")

has_quotes = '"' in key or "'" in key
print(f"Has quotes: {has_quotes}")

# Show hex of last few chars
if key:
    print(f"Last 5 chars (repr): {repr(key[-5:])}")

# Try stripping whitespace
stripped = key.strip()
if stripped != key:
    print(f"WARNING: Key has leading/trailing whitespace!")
    print(f"After strip length: {len(stripped)}")

# Check if key has invalid chars
import re
invalid = re.findall(r'[^a-zA-Z0-9_-]', key)
if invalid:
    print(f"WARNING: Key contains non-standard chars: {set(invalid)}")

# Try different base URLs
urls = [
    "https://api.moonshot.ai/v1",
    "https://api.moonshot.cn/v1",
]

import openai
for url in urls:
    print(f"\nTrying {url}...")
    try:
        client = openai.OpenAI(api_key=stripped or key, base_url=url)
        resp = client.chat.completions.create(
            model="kimi-k2.6",
            messages=[{"role": "user", "content": "Hello"}],
            max_tokens=10
        )
        print(f"  SUCCESS: {resp.choices[0].message.content}")
        break
    except Exception as e:
        print(f"  FAILED: {type(e).__name__}: {str(e)[:100]}")

# Also try model variants
models = ["kimi-k2.6", "kimi-k2", "kimi-latest", "moonshot-v1-8k"]
print("\n=== Testing different model names ===")
for model in models:
    try:
        client = openai.OpenAI(api_key=stripped or key, base_url="https://api.moonshot.ai/v1")
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Hello"}],
            max_tokens=10
        )
        print(f"  {model}: SUCCESS")
    except Exception as e:
        print(f"  {model}: {str(e)[:80]}")
