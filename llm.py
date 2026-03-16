from langchain_nvidia_ai_endpoints import ChatNVIDIA


client = ChatNVIDIA(
  model="nvidia/nemotron-3-super-120b-a12b",
  api_key="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", 
  temperature=1,
  top_p=0.95,
  max_tokens=16384,
  reasoning_budget=16384,
  chat_template_kwargs={"enable_thinking":True},
)

for chunk in client.stream([{"role":"user","content":""}]):
  
    if chunk.additional_kwargs and "reasoning_content" in chunk.additional_kwargs:
      print(chunk.additional_kwargs["reasoning_content"], end="")
  
    print(chunk.content, end="")

