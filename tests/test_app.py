import pytest
import asyncio

@pytest.mark.asyncio
async def test_index(test_app):
  client = test_app.test_client()
  response = await client.get('/')
  assert response.status_code == 200

