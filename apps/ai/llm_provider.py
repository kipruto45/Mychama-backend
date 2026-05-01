# MyChama AI Chatbot - LLM Provider Abstraction
# apps/ai/llm_provider.py

import os
import logging
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Generator, Optional
from django.conf import settings

logger = logging.getLogger(__name__)


class BaseLLMProvider(ABC):
    """Abstract base class for LLM providers"""
    
    @abstractmethod
    def call(
        self,
        system_prompt: str,
        messages: List[Dict[str, str]],
        tools: List[Dict],
        temperature: float = 0.7,
        max_tokens: int = 2000
    ) -> Dict[str, Any]:
        """
        Call LLM with messages and tools.
        
        Returns:
            Dict with 'content' and 'tool_calls'
        """
        pass
    
    @abstractmethod
    def stream_call(
        self,
        system_prompt: str,
        messages: List[Dict[str, str]],
        tools: List[Dict],
        temperature: float = 0.7,
        max_tokens: int = 2000
    ) -> Generator[str, None, None]:
        """
        Stream response from LLM.
        
        Yields:
            Token strings
        """
        pass


class OpenAIProvider(BaseLLMProvider):
    """OpenAI API provider with production-grade configuration"""
    
    def __init__(self):
        try:
            import openai
            api_key = getattr(settings, 'OPENAI_API_KEY', os.getenv('OPENAI_API_KEY'))
            if not api_key or api_key.startswith('sk-proj-your'):
                raise ValueError("OPENAI_API_KEY not configured. Set it in .env or settings.")
            
            self.client = openai.OpenAI(api_key=api_key)
            self.timeout = getattr(settings, 'OPENAI_TIMEOUT_SECONDS', 30)
        except ImportError:
            logger.error("OpenAI not installed. Install with: pip install openai")
            self.client = None
        except ValueError as e:
            logger.warning(f"OpenAI client warning: {e}")
            self.client = None
        
        self.model = getattr(settings, 'AI_CHAT_MODEL', 'gpt-4-turbo-preview')
        logger.info(f"OpenAI provider initialized with model: {self.model}")
    
    def call(
        self,
        system_prompt: str,
        messages: List[Dict[str, str]],
        tools: List[Dict],
        temperature: float = 0.7,
        max_tokens: int = 2000
    ) -> Dict[str, Any]:
        """Call OpenAI API with production safety checks"""
        if not self.client:
            raise RuntimeError("OpenAI client not initialized. Check OPENAI_API_KEY configuration.")
        
        try:
            # Prepare messages with system prompt
            full_messages = [
                {"role": "system", "content": system_prompt},
                *messages
            ]
            
            # Prepare tools
            function_definitions = [
                {
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool.get("description", ""),
                        "parameters": tool.get("parameters", {})
                    }
                }
                for tool in tools
            ]
            
            response = self.client.chat.completions.create(
                model=self.model,
                messages=full_messages,
                tools=function_definitions if function_definitions else None,
                temperature=temperature,
                max_tokens=min(max_tokens, 4096),  # Enforce token limit
                timeout=self.timeout
            )
            
            # Extract response
            content = ""
            tool_calls = []
            
            for choice in response.choices:
                if choice.message.content:
                    content += choice.message.content
                
                if hasattr(choice.message, 'tool_calls') and choice.message.tool_calls:
                    for tool_call in choice.message.tool_calls:
                        tool_calls.append({
                            'name': tool_call.function.name,
                            'arguments': tool_call.function.arguments
                        })
            
            return {
                'content': content,
                'tool_calls': tool_calls,
                'usage': {
                    'prompt_tokens': response.usage.prompt_tokens,
                    'completion_tokens': response.usage.completion_tokens
                }
            }
        
        except ValueError as e:
            logger.error(f"OpenAI validation error: {e}")
            raise RuntimeError(f"API validation failed: {str(e)}")
        except Exception as e:
            logger.error(f"OpenAI API error: {e}", exc_info=True)
            raise RuntimeError(f"Failed to get AI response: {str(e)}")
    
    def stream_call(
        self,
        system_prompt: str,
        messages: List[Dict[str, str]],
        tools: List[Dict],
        temperature: float = 0.7,
        max_tokens: int = 2000
    ) -> Generator[str, None, None]:
        """Stream response from OpenAI"""
        if not self.client:
            raise RuntimeError("OpenAI client not initialized")
        
        try:
            full_messages = [
                {"role": "system", "content": system_prompt},
                *messages
            ]
            
            with self.client.chat.completions.create(
                model=self.model,
                messages=full_messages,
                temperature=temperature,
                max_tokens=min(max_tokens, 4096),
                stream=True,
                timeout=self.timeout
            ) as stream:
                for event in stream:
                    if hasattr(event, 'choices') and event.choices:
                        delta = event.choices[0].delta
                        if hasattr(delta, 'content') and delta.content:
                            yield delta.content
        
        except Exception as e:
            logger.error(f"OpenAI streaming error: {e}", exc_info=True)
            raise RuntimeError(f"Failed to stream AI response: {str(e)}")


class AnthropicProvider(BaseLLMProvider):
    """Anthropic Claude provider"""
    
    def __init__(self):
        try:
            import anthropic
            self.client = anthropic.Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))
        except ImportError:
            logger.error("Anthropic not installed. Install with: pip install anthropic")
            self.client = None
        
        self.model = os.getenv('ANTHROPIC_MODEL', 'claude-3-opus-20240229')
    
    def call(
        self,
        system_prompt: str,
        messages: List[Dict[str, str]],
        tools: List[Dict],
        temperature: float = 0.7,
        max_tokens: int = 2000
    ) -> Dict[str, Any]:
        """Call Anthropic API"""
        if not self.client:
            raise RuntimeError("Anthropic client not initialized")
        
        try:
            # Prepare tools
            tool_definitions = [
                {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "input_schema": tool.get("parameters", {})
                }
                for tool in tools
            ]
            
            response = self.client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system_prompt,
                tools=tool_definitions if tool_definitions else None,
                messages=messages,
                temperature=temperature
            )
            
            # Extract response
            content = ""
            tool_calls = []
            
            for block in response.content:
                if hasattr(block, 'text'):
                    content += block.text
                elif block.type == "tool_use":
                    tool_calls.append({
                        'name': block.name,
                        'arguments': block.input
                    })
            
            return {
                'content': content,
                'tool_calls': tool_calls,
                'usage': {
                    'prompt_tokens': response.usage.input_tokens,
                    'completion_tokens': response.usage.output_tokens
                }
            }
        
        except Exception as e:
            logger.error(f"Anthropic API error: {e}")
            raise
    
    def stream_call(
        self,
        system_prompt: str,
        messages: List[Dict[str, str]],
        tools: List[Dict],
        temperature: float = 0.7,
        max_tokens: int = 2000
    ) -> Generator[str, None, None]:
        """Stream response from Anthropic"""
        if not self.client:
            raise RuntimeError("Anthropic client not initialized")
        
        try:
            tool_definitions = [
                {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "input_schema": tool.get("parameters", {})
                }
                for tool in tools
            ]
            
            with self.client.messages.stream(
                model=self.model,
                max_tokens=max_tokens,
                system=system_prompt,
                tools=tool_definitions if tool_definitions else None,
                messages=messages,
                temperature=temperature
            ) as stream:
                for text in stream.text_stream:
                    yield text
        
        except Exception as e:
            logger.error(f"Anthropic streaming error: {e}")
            raise


class MockLLMProvider(BaseLLMProvider):
    """Mock provider for testing"""
    
    def call(
        self,
        system_prompt: str,
        messages: List[Dict[str, str]],
        tools: List[Dict],
        temperature: float = 0.7,
        max_tokens: int = 2000
    ) -> Dict[str, Any]:
        """Return mock response"""
        return {
            'content': "This is a mock response from the chatbot.",
            'tool_calls': [],
            'usage': {
                'prompt_tokens': 100,
                'completion_tokens': 50
            }
        }
    
    def stream_call(
        self,
        system_prompt: str,
        messages: List[Dict[str, str]],
        tools: List[Dict],
        temperature: float = 0.7,
        max_tokens: int = 2000
    ) -> Generator[str, None, None]:
        """Yield mock tokens"""
        words = ["This", " is", " a", " mock", " response", " from", " the", " chatbot."]
        for word in words:
            yield word


def get_llm_provider() -> BaseLLMProvider:
    """
    Get LLM provider based on configuration.
    
    Priority:
    1. OPENAI_API_KEY -> OpenAI
    2. ANTHROPIC_API_KEY -> Anthropic
    3. Mock (for testing)
    """
    if os.getenv('OPENAI_API_KEY'):
        logger.info("Using OpenAI provider")
        return OpenAIProvider()
    elif os.getenv('ANTHROPIC_API_KEY'):
        logger.info("Using Anthropic provider")
        return AnthropicProvider()
    else:
        logger.warning("No LLM API key found. Using mock provider.")
        return MockLLMProvider()
