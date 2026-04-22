"""Diagnose Kimi API key issue."""
import os
import sys

# Add parent to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load .env first
try:
    from dotenv import load_dotenv
    load_dotenv()
    print("✓ dotenv loaded")
except ImportError:
    print("✗ dotenv not available")

print("\n=== Environment Variables ===")
print(f"MOONSHOT_API_KEY: {'SET (' + os.environ.get('MOONSHOT_API_KEY', '')[:10] + '...)' if os.environ.get('MOONSHOT_API_KEY') else 'NOT SET'}")
print(f"XAI_API_KEY:      {'SET (' + os.environ.get('XAI_API_KEY', '')[:10] + '...)' if os.environ.get('XAI_API_KEY') else 'NOT SET'}")
print(f"AI_MODEL:         {os.environ.get('AI_MODEL', 'NOT SET')}")
print(f"AI_BASE_URL:      {os.environ.get('AI_BASE_URL', 'NOT SET')}")

print("\n=== CONFIG Check ===")
from config import CONFIG, get_ai_api_key, get_ai_base_url, get_ai_model, create_ai_client

api_key = get_ai_api_key()
print(f"get_ai_api_key(): {'SET (' + api_key[:10] + '...)' if api_key else 'EMPTY/NOT SET'}")
print(f"get_ai_base_url(): {get_ai_base_url()}")
print(f"get_ai_model(): {get_ai_model()}")

print(f"\nCONFIG['MOONSHOT_API_KEY']: {'SET' if CONFIG.get('MOONSHOT_API_KEY') and CONFIG.get('MOONSHOT_API_KEY') != 'YOUR_MOONSHOT_API_KEY' else 'NOT SET/PLACEHOLDER'}")
print(f"CONFIG['XAI_API_KEY']: {'SET' if CONFIG.get('XAI_API_KEY') and CONFIG.get('XAI_API_KEY') != 'YOUR_XAI_API_KEY' else 'NOT SET/PLACEHOLDER'}")

print("\n=== Testing API Call ===")
try:
    client = create_ai_client()
    print(f"Client created with base_url: {client.base_url}")
    
    # Try a simple completion
    response = client.chat.completions.create(
        model=get_ai_model(),
        messages=[{"role": "user", "content": "Say 'hello'"}],
        max_tokens=10
    )
    print(f"✓ API call successful! Response: {response.choices[0].message.content}")
except Exception as e:
    print(f"✗ API call failed: {type(e).__name__}: {e}")
    
print("\n=== .env file check ===")
env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
if os.path.exists(env_path):
    print(f"✓ .env file exists at: {env_path}")
    with open(env_path, 'r') as f:
        content = f.read()
    if 'MOONSHOT_API_KEY' in content:
        print("✓ MOONSHOT_API_KEY found in .env")
        # Extract value
        for line in content.split('\n'):
            if line.startswith('MOONSHOT_API_KEY'):
                print(f"  Line: {line.split('=')[0]}={line.split('=')[1][:10] + '...' if '=' in line and len(line.split('=')) > 1 else 'EMPTY'}")
    else:
        print("✗ MOONSHOT_API_KEY NOT found in .env")
    if 'XAI_API_KEY' in content:
        print("✓ XAI_API_KEY found in .env")
    else:
        print("✗ XAI_API_KEY NOT found in .env")
else:
    print(f"✗ .env file NOT found at: {env_path}")
