import anthropic

with open("apikey.txt") as f:
    key = f.read().strip()

client = anthropic.Anthropic(api_key=key)

response = client.messages.create(
    model="claude-sonnet-4-5",
    max_tokens=50,
    messages=[{"role": "user", "content": "Say 'API working' and nothing else."}]
)
print(response.content[0].text)