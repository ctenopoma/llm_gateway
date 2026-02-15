
import pytest
from unittest.mock import AsyncMock, patch
from uuid import uuid4
from fastapi.testclient import TestClient

from app.main import app
# We need to ensure startup didn't happen to interact with real DB, 
# but TestClient triggers startup. 
# We'll mock lifespan or init_db/init_redis inside app.main so verify_admin doesn't crash during startup.

client = TestClient(app)

@pytest.mark.asyncio
async def test_delete_api_key_authed():
    key_id = str(uuid4())
    
    # Mock database interactions and token verification
    with patch("app.routers.admin.db.fetch_one", new_callable=AsyncMock) as mock_fetch_one, \
         patch("app.routers.admin.db.execute", new_callable=AsyncMock) as mock_execute, \
         patch("app.routers.admin._verify_token", return_value=True):

        # probe returns a row (key exists)
        mock_fetch_one.return_value = {"id": uuid4(), "hashed_key": "x" * 64}
        # simulate successful delete
        mock_execute.return_value = "DELETE 1"

        response = client.delete(
            f"/admin/api/api-keys/{key_id}",
            cookies={"admin_token": "valid-token"}
        )

        assert response.status_code == 200
        assert response.json() == {"status": "deleted"}
        
        # Verify DB call
        mock_execute.assert_called_once()
        args = mock_execute.call_args[0] 
        # args[0] is query, args[1] is UUID
        assert "DELETE FROM ApiKeys WHERE id = $1" in args[0]
        assert str(args[1]) == key_id

@pytest.mark.asyncio
async def test_delete_api_key_not_found():
    key_id = str(uuid4())
    
    with patch("app.routers.admin.db.fetch_one", new_callable=AsyncMock) as mock_fetch_one, \
         patch("app.routers.admin.db.fetch_all", new_callable=AsyncMock) as mock_fetch_all, \
         patch("app.routers.admin.db.execute", new_callable=AsyncMock) as mock_execute, \
         patch("app.routers.admin._verify_token", return_value=True):
     
        # probe returns None (key not found)
        mock_fetch_one.return_value = None
        mock_fetch_all.return_value = []

        response = client.delete(
            f"/admin/api/api-keys/{key_id}",
            cookies={"admin_token": "valid-token"}
        )

        assert response.status_code == 404
        assert response.json()["detail"] == "API key not found"

def test_delete_api_key_unauthorized():
    key_id = str(uuid4())
    # No auth cookie
    response = client.delete(f"/admin/api/api-keys/{key_id}")
    assert response.status_code == 401
