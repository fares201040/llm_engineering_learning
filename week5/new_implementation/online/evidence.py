"""User-visible evidence associated with an answered turn."""

from pydantic import BaseModel


class Result(BaseModel):
    page_content: str
    metadata: dict
