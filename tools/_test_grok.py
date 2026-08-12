"""Test xAI Grok API key and model."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import dotenv_values
import openai

v = dotenv_values(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))
k = v.get("XAI_API_KEY", "")
print(f"XAI key prefix: {k[:15]}... len={len(k)}")

c = openai.OpenAI(api_key=k, base_url="https://api.x.ai/v1")

try:
    r = c.chat.completions.create(
        model="grok-4.6",
        max_tokens=50,
        temperature=0.3,
        messages=[
            {"role": "system", "content": "Output strict JSON only."},
            {"role": "user", "content": 'Return JSON: {"grade": "A", "edgeProbability": 75}'},
        ],
        response_format={"type": "json_object"},
    )
    print("SUCCESS:", repr(r.choices[0].message.content))
except Exception as e:
    print("ERROR:", e)
