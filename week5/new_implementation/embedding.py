"""Configurable embeddings and model-isolated Chroma collection names."""

from functools import lru_cache
from hashlib import sha256


@lru_cache(maxsize=4)
def get_embeddings(model_name: str, provider: str = "huggingface"):
    if provider == "huggingface":
        from langchain_huggingface import HuggingFaceEmbeddings

        return HuggingFaceEmbeddings(model_name=model_name)
    if provider == "openai":
        from langchain_openai import OpenAIEmbeddings

        return OpenAIEmbeddings(model=model_name)
    raise ValueError(f"unsupported embedding provider: {provider}")


def split_for_embedding(
    text: str, prefix: str, embeddings, *, provider: str = "huggingface"
) -> list[tuple[str, int]]:
    """Split with the selected provider's tokenizer and input limit."""
    if provider == "openai":
        import tiktoken

        tokenizer = tiktoken.encoding_for_model(embeddings.model)
        max_tokens = embeddings.embedding_ctx_length
        encode = tokenizer.encode
        decode = tokenizer.decode
    elif provider == "huggingface":
        model = embeddings._client
        tokenizer = model.tokenizer
        max_tokens = model.max_seq_length - tokenizer.num_special_tokens_to_add(
            pair=False
        )

        def encode(value):
            return tokenizer.encode(value, add_special_tokens=False, verbose=False)

        def decode(values):
            return tokenizer.decode(
                values, skip_special_tokens=True, clean_up_tokenization_spaces=False
            )
    else:
        raise ValueError(f"unsupported embedding provider: {provider}")
    tokens = encode(text)
    if len(tokens) <= max_tokens:
        return [(text, len(tokens))]

    prefix_text = f"{prefix}\n\n" if prefix else ""
    prefix_tokens = encode(prefix_text)
    part_size = max_tokens - len(prefix_tokens) - 4
    if part_size < 1:
        raise ValueError("embedding identity prefix exceeds the model sequence limit")

    parts = []
    for start in range(0, len(tokens), part_size):
        body = decode(tokens[start : start + part_size])
        part = prefix_text + body
        count = len(encode(part))
        if count > max_tokens:
            raise ValueError("embedding part exceeds the model sequence limit")
        parts.append((part, count))
    return parts


def collection_name_for_model(base: str, model_name: str, purpose: str) -> str:
    model_id = sha256(model_name.encode("utf-8")).hexdigest()[:12]
    return f"{base}_{purpose}_{model_id}"
