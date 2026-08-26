-- Run once after all four known workers have registered. Administrative policy,
-- intentionally NOT embedded in Alembic. Lower number = preferred acquisition.
INSERT INTO cluster_worker_roles (worker_id, role_name, enabled, priority, created_at, updated_at)
SELECT worker_id, 'discord', TRUE,
       CASE worker_id WHEN 'rnt-01' THEN 10 WHEN 'mak-01' THEN 20 WHEN 'kah-01' THEN 30 WHEN 'hnl-01' THEN 40 END,
       now(), now()
FROM cluster_workers
WHERE worker_id IN ('rnt-01','mak-01','kah-01','hnl-01')
ON CONFLICT (worker_id, role_name) DO UPDATE
SET enabled=TRUE, priority=EXCLUDED.priority, updated_at=now();

-- Configure these privately for the operator's guild/channel. Leave disabled
-- until the IDs have been set and verified.
-- UPDATE cluster_runtime_settings SET setting_value='<guild id>', updated_at=now() WHERE setting_key='operator.discord_guild_id' AND scope_type='global' AND scope_name='';
-- UPDATE cluster_runtime_settings SET setting_value='<channel id>', updated_at=now() WHERE setting_key='operator.discord_channel_id' AND scope_type='global' AND scope_name='';
-- UPDATE cluster_runtime_settings SET setting_value='true', updated_at=now() WHERE setting_key='operator.notifications_enabled' AND scope_type='global' AND scope_name='';
