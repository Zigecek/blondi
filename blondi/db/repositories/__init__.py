"""Repository pattern — tenká vrstva nad SQLAlchemy ORM.

Každá repository dostává Session v konstruktoru nebo jako argument metody.
Nevolá commit sama — to patří do volajícího service/UI kódu.
"""
