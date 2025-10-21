import os
from openai import OpenAI
from tooliscode import Client


tooliscode_client = Client(
    client = OpenAI(
    # This is the default and can be omitted
    api_key=os.environ.get("OPENAI_API_KEY"),
))


response = tooliscode_client.create(
    model="gpt-4.1",
    instructions="You are a coding assistant that talks like a pirate.",
    input="How do I check if a Python object is an instance of a class?",
    tools=[
        {
            "type": "function",
            "function": {
                "name": "get_current_weather",
                "description": "Get the current weather",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "location": {
                            "type": "string",
                            "description": "The location to get the weather for",
                        },
                    },
                    "required": ["location"],
                },
            },
        }
    ],
)

print(response.output_text)
