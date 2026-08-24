#!/usr/bin/env python3
"""
EchoServe CPU Mode - Lightweight Local LLM Server
Runs Qwen/Qwen2.5-0.5B-Instruct or similar tiny models on CPU.
Provides OpenAI-compatible API for EchoServe LLMPlugin.
"""
from __future__ import annotations

import os
import sys
import json
import logging
import argparse
from typing import List, Dict, Any, Optional, AsyncIterator
from pathlib import Path

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("cpu_llm_server")

# Try to use transformers (most compatible)
try:
    from transformers import AutoModelForCausalLM, AutoTokenizer, TextIteratorStreamer
    import torch
    TRANSFORMERS_AVAILABLE = True
except ImportError:
    TRANSFORMERS_AVAILABLE = False
    logger.warning("transformers not installed. Will try to install.")

# FastAPI for HTTP server
try:
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import StreamingResponse
    import uvicorn
    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False


def install_dependencies():
    """Install required packages."""
    import subprocess
    packages = ["torch", "transformers", "fastapi", "uvicorn", "accelerate"]
    logger.info(f"Installing dependencies: {packages}")
    subprocess.check_call([sys.executable, "-m", "pip", "install"] + packages)
    logger.info("Dependencies installed. Please restart the server.")
    sys.exit(0)


class CPULLMServer:
    """CPU-based LLM server with OpenAI-compatible API."""

    def __init__(self, model_name: str = "Qwen/Qwen2.5-0.5B-Instruct", device: str = "cpu"):
        self.model_name = model_name
        self.device = device
        self.model = None
        self.tokenizer = None
        self.app = FastAPI(title="EchoServe CPU LLM Server", version="0.1.0")
        self._setup_routes()

    def load_model(self) -> None:
        """Load model and tokenizer."""
        if not TRANSFORMERS_AVAILABLE:
            raise RuntimeError("transformers library not available")

        logger.info(f"Loading model: {self.model_name}")
        logger.info(f"Device: {self.device}")
        logger.info("This may take a few minutes on first run (downloading model)...")

        # Load tokenizer
        self.tokenizer = AutoTokenizer.from_pretrained(
            self.model_name,
            trust_remote_code=True,
            padding_side="left",
        )

        # Load model with CPU optimizations
        load_kwargs = {
            "trust_remote_code": True,
            "torch_dtype": torch.float32,  # CPU only supports float32
        }

        # Try to use device map for CPU
        if self.device == "cpu":
            load_kwargs["device_map"] = "cpu"
            load_kwargs["low_cpu_mem_usage"] = True

        self.model = AutoModelForCausalLM.from_pretrained(
            self.model_name,
            **load_kwargs,
        )

        # Set pad token if missing
        if self.tokenizer.pad_token is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token

        logger.info(f"Model loaded successfully")
        logger.info(f"Parameters: {sum(p.numel() for p in self.model.parameters()) / 1e6:.1f}M")

    def _setup_routes(self) -> None:
        """Setup FastAPI routes."""

        @self.app.get("/v1/models")
        async def list_models():
            """List available models (OpenAI compatible)."""
            return {
                "object": "list",
                "data": [
                    {
                        "id": self.model_name,
                        "object": "model",
                        "created": 1700000000,
                        "owned_by": "echoseve-cpu",
                    }
                ],
            }

        @self.app.post("/v1/chat/completions")
        async def chat_completion(request_data: Dict[str, Any]):
            """Chat completion endpoint (OpenAI compatible)."""
            if self.model is None or self.tokenizer is None:
                raise HTTPException(status_code=503, detail="Model not loaded")

            model_id = request_data.get("model", self.model_name)
            messages = request_data.get("messages", [])
            temperature = request_data.get("temperature", 0.7)
            max_tokens = request_data.get("max_tokens", 2048)
            top_p = request_data.get("top_p", 0.9)
            stream = request_data.get("stream", False)
            stop = request_data.get("stop", None)

            if not messages:
                raise HTTPException(status_code=400, detail="No messages provided")

            # Build prompt from messages
            prompt = self._build_prompt(messages)

            if stream:
                return StreamingResponse(
                    self._generate_stream(prompt, temperature, max_tokens, top_p, stop),
                    media_type="text/event-stream",
                )
            else:
                response_text = self._generate(prompt, temperature, max_tokens, top_p, stop)
                prompt_tokens = len(self.tokenizer.encode(prompt))
                completion_tokens = len(self.tokenizer.encode(response_text))

                return {
                    "id": "chatcmpl-echoseve-cpu",
                    "object": "chat.completion",
                    "created": 1700000000,
                    "model": model_id,
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": response_text,
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "total_tokens": prompt_tokens + completion_tokens,
                    },
                }

    def _build_prompt(self, messages: List[Dict[str, str]]) -> str:
        """Build prompt from message list."""
        # For Qwen models, use the chat template
        if hasattr(self.tokenizer, "apply_chat_template"):
            try:
                return self.tokenizer.apply_chat_template(
                    messages,
                    tokenize=False,
                    add_generation_prompt=True,
                )
            except Exception as e:
                logger.warning(f"apply_chat_template failed: {e}, falling back to manual")

        # Manual fallback
        prompt_parts = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "system":
                prompt_parts.append(f"System: {content}")
            elif role == "user":
                prompt_parts.append(f"User: {content}")
            elif role == "assistant":
                prompt_parts.append(f"Assistant: {content}")
        prompt_parts.append("Assistant:")
        return "\n".join(prompt_parts)

    def _generate(
        self,
        prompt: str,
        temperature: float,
        max_tokens: int,
        top_p: float,
        stop: Optional[List[str]],
    ) -> str:
        """Generate text synchronously."""
        inputs = self.tokenizer(prompt, return_tensors="pt", padding=True)
        inputs = {k: v.to(self.device) for k, v in inputs.items()}

        input_length = inputs["input_ids"].shape[1]
        max_length = min(input_length + max_tokens, 2048)  # Hard limit

        with torch.no_grad():
            outputs = self.model.generate(
                **inputs,
                max_length=max_length,
                temperature=temperature,
                top_p=top_p,
                do_sample=temperature > 0,
                pad_token_id=self.tokenizer.pad_token_id,
                eos_token_id=self.tokenizer.eos_token_id,
            )

        # Decode only the new tokens
        new_tokens = outputs[0][input_length:]
        response = self.tokenizer.decode(new_tokens, skip_special_tokens=True)

        # Apply stop sequences
        if stop:
            for stop_seq in stop:
                if stop_seq in response:
                    response = response[: response.index(stop_seq)]

        return response.strip()

    async def _generate_stream(
        self,
        prompt: str,
        temperature: float,
        max_tokens: int,
        top_p: float,
        stop: Optional[List[str]],
    ) -> AsyncIterator[str]:
        """Generate text streamingly."""
        # For simplicity, generate full text then stream it word by word
        # True token-by-token streaming requires more complex handling
        response = self._generate(prompt, temperature, max_tokens, top_p, stop)

        # Stream word by word with small delays
        import asyncio

        words = response.split(" ")
        current_text = ""

        for word in words:
            current_text += word + " "
            data = {
                "id": "chatcmpl-echoseve-cpu",
                "object": "chat.completion.chunk",
                "created": 1700000000,
                "model": self.model_name,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": word + " "},
                        "finish_reason": None,
                    }
                ],
            }
            yield f"data: {json.dumps(data)}\n\n"
            await asyncio.sleep(0.01)  # Small delay for streaming effect

        # Final chunk
        yield "data: [DONE]\n\n"

    def run(self, host: str = "0.0.0.0", port: int = 8000) -> None:
        """Run the server."""
        self.load_model()
        logger.info(f"Starting server on {host}:{port}")
        uvicorn.run(self.app, host=host, port=port, log_level="info")


def main():
    parser = argparse.ArgumentParser(description="EchoServe CPU LLM Server")
    parser.add_argument(
        "--model",
        type=str,
        default="Qwen/Qwen2.5-0.5B-Instruct",
        help="Model name or path (default: Qwen/Qwen2.5-0.5B-Instruct)",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="0.0.0.0",
        help="Server host (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Server port (default: 8000)",
    )
    parser.add_argument(
        "--device",
        type=str,
        default="cpu",
        choices=["cpu", "cuda"],
        help="Device to use (default: cpu)",
    )
    parser.add_argument(
        "--install",
        action="store_true",
        help="Install dependencies and exit",
    )

    args = parser.parse_args()

    if args.install:
        install_dependencies()
        return

    if not TRANSFORMERS_AVAILABLE or not FASTAPI_AVAILABLE:
        logger.error("Missing dependencies. Run with --install flag.")
        logger.error("Command: python cpu_llm_server.py --install")
        sys.exit(1)

    server = CPULLMServer(model_name=args.model, device=args.device)
    server.run(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
