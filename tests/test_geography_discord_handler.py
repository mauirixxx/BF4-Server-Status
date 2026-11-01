import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import pytest
from db import SessionLocal
from models import BF4Server, GuildServer
import serverwatcher as sw

GUID='28773abe-e620-4d36-9512-c6f4b128f0ad'

@pytest.mark.asyncio
async def test_addserver_handler_new_and_existing():
    guild=SimpleNamespace(id=87654321)
    interaction=SimpleNamespace(guild=guild, channel=SimpleNamespace(id=5), user=SimpleNamespace(id=9), followup=SimpleNamespace(send=AsyncMock()))
    async def fake_validation(guid):return None
    with patch.object(sw,'prepare_management',new=AsyncMock(return_value=True)), patch.object(sw,'configured_announcement_channels',return_value=[]), patch.object(sw,'request_server_lifecycle_revalidation',return_value=None), patch.object(sw,'get_keeper_snapshot_authoritative',new=fake_validation), patch.object(sw,'audit_command'), patch.object(sw,'rate_limited_battlelog_to_thread',new=AsyncMock(return_value=30)):
        await sw.addserver.callback(interaction,GUID)
        with SessionLocal() as session:
            row=session.get(BF4Server,GUID)
            gs=session.get(GuildServer,(guild.id,GUID))
            print("HANDLER_REPLY",interaction.followup.send.await_args_list)
            assert row is not None and gs is not None
            assert row.geo_next_check_at is not None
            assert row.geo_checked_at is None
        await sw.addserver.callback(interaction,GUID)
        with SessionLocal() as session:
            assert session.get(GuildServer,(guild.id,GUID)) is not None
        assert interaction.followup.send.await_count == 2
