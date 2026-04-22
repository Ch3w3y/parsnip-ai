"""
title: Joplin Deep-Link Formatter
author: parsnip-ai
author_url: https://github.com/Ch3w3y/parsnip-ai
description: Transforms raw joplin:// callback links into nice clickable buttons.
version: 0.1.0
"""

import re
from typing import Optional

class Filter:
    def __init__(self):
        pass

    def outlet(self, body: dict, __user__: Optional[dict] = None) -> dict:
        """
        Processes the assistant response to format Joplin links.
        """
        if "messages" not in body or len(body["messages"]) == 0:
            return body

        last_msg = body["messages"][-1]
        if last_msg.get("role") != "assistant":
            return body

        content = last_msg.get("content", "")
        
        # Regex to find joplin://x-callback-url/openNote?id=...
        joplin_pattern = r"joplin://x-callback-url/openNote\?id=([a-f0-9]+)"
        
        def replace_link(match):
            note_id = match.group(1)
            # Create a stylized markdown button-like link
            return f"\n\n[📂 **Open in Joplin Application**]({match.group(0)})\n"

        # Apply transformation
        new_content = re.sub(joplin_pattern, replace_link, content)
        
        # Update the message content
        last_msg["content"] = new_content
        
        return body
