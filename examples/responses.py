import os
import json
from re import I
from typing import Any
from openai import OpenAI
from openai.types.responses import ResponseFunctionCallOutputItem
from tooliscode import ToolIsCode


client = OpenAI()


def callback(req_id: str, function_name: str, args: str) -> dict[str, Any]:
    print(f"function_call: {req_id=} {function_name=} {args=}")
    assert function_name == "get_current_weather"
    return "Weather is 22 'c."


tic_client = ToolIsCode(
    tools=[
        {
            "type": "function",
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
        }
    ],
    callback=callback,
)

response = client.responses.create(
    model="gpt-5-mini",
    instructions=tic_client.instructions,
    input="Get me the weather in SF",
    tools=tic_client.tools,
)

while True:
    fc = response.output[-1].to_dict()
    if fc.get("type") != "function_call":
        print(response.output_text)
        break

    print("GOT", fc)
    fc_resp = tic_client.tool_call(fc)
    print("RETURNING", fc_resp)

    response = client.responses.create(
        model="gpt-5-mini",
        instructions=tic_client.instructions,
        previous_response_id=response.id,
        input=[fc_resp],
        tools=tic_client.tools,
    )
