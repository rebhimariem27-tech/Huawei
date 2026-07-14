from groq import Groq
import base64, os
# Remplace par ta clé
client = Groq(api_key="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")

r = client.chat.completions.create(
    model='meta-llama/llama-4-scout-17b-16e-instruct',
    messages=[{'role':'user','content':'Dis bonjour en français en une ligne'}],
    max_tokens=50
)
print(r.choices[0].message.content)