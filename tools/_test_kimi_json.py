"""Test what Kimi k2.6 actually returns for a JSON request."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import dotenv_values
import openai

v = dotenv_values(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))
k = v.get("MOONSHOT_API_KEY", "")
print(f"Key prefix: {k[:15]}... len={len(k)}")

c = openai.OpenAI(api_key=k, base_url="https://api.moonshot.ai/v1")

# Test 1: with response_format json_object
print("\n--- Test 1: response_format=json_object ---")
try:
    r = c.chat.completions.create(
        model="kimi-k2.6",
        max_tokens=200,
        temperature=1,
        messages=[
            {"role": "system", "content": "Output strict JSON only."},
            {"role": "user", "content": "Return JSON with keys grade and edgeProbability."},
        ],
        response_format={"type": "json_object"},
    )
    print("RAW:", repr(r.choices[0].message.content))
except Exception as e:
    print("ERROR:", e)

# Test 2: without response_format
print("\n--- Test 2: no response_format ---")
try:
    r2 = c.chat.completions.create(
        model="kimi-k2.6",
        max_tokens=200,
        temperature=1,
        messages=[
            {"role": "system", "content": "Output strict JSON only."},
            {"role": "user", "content": "Return JSON with keys grade and edgeProbability."},
        ],
    )
    print("RAW:", repr(r2.choices[0].message.content))
except Exception as e:
    print("ERROR:", e)
