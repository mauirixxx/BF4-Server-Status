--
-- PostgreSQL database dump
--

\restrict dU6v6wPezc1lorrrc0s19cxAaYjmrfPLW2aVQAJnQmofJYSLOLy2ZVxwQd7m6Xv

-- Dumped from database version 16.15 (Ubuntu 16.15-0ubuntu0.24.04.1)
-- Dumped by pg_dump version 16.15 (Ubuntu 16.15-0ubuntu0.24.04.1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: alembic_version; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.alembic_version (
    version_num character varying(32) NOT NULL
);


--
-- Name: bf4_maps; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.bf4_maps (
    map_key character varying(100) NOT NULL,
    map_name character varying(255) NOT NULL
);


--
-- Name: bf4_player_aliases; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.bf4_player_aliases (
    id bigint NOT NULL,
    platform character varying(32) NOT NULL,
    persona_id bigint NOT NULL,
    player_name character varying(255) NOT NULL,
    normalized_name character varying(255) NOT NULL,
    first_seen timestamp with time zone NOT NULL,
    last_seen timestamp with time zone NOT NULL
);


--
-- Name: bf4_player_aliases_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.bf4_player_aliases_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: bf4_player_aliases_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.bf4_player_aliases_id_seq OWNED BY public.bf4_player_aliases.id;


--
-- Name: bf4_player_sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.bf4_player_sessions (
    id bigint NOT NULL,
    server_guid character varying(36) NOT NULL,
    platform character varying(32) NOT NULL,
    map_key character varying(100),
    map_name character varying(255) NOT NULL,
    persona_id bigint,
    player_name character varying(255) NOT NULL,
    normalized_name character varying(255) NOT NULL,
    time_joined timestamp with time zone NOT NULL,
    last_seen timestamp with time zone NOT NULL,
    time_left timestamp with time zone,
    persona_alert_mode character varying(16)
);


--
-- Name: bf4_player_sessions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.bf4_player_sessions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: bf4_player_sessions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.bf4_player_sessions_id_seq OWNED BY public.bf4_player_sessions.id;


--
-- Name: bf4_servers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.bf4_servers (
    server_guid character varying(36) NOT NULL,
    server_name character varying(255) NOT NULL,
    platform character varying(32) DEFAULT 'Unknown'::character varying NOT NULL,
    battlelog_url text,
    platform_source character varying(64),
    tick_rate_hz integer
);


--
-- Name: cluster_handoff_requests; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cluster_handoff_requests (
    id bigint NOT NULL,
    lease_key character varying(255) NOT NULL,
    lease_type character varying(64) NOT NULL,
    source_worker_id character varying(100),
    target_worker_id character varying(100),
    expected_generation bigint NOT NULL,
    status character varying(32) DEFAULT 'pending'::character varying NOT NULL,
    requested_by character varying(255),
    requested_at timestamp with time zone NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    failure_reason text,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);


--
-- Name: cluster_handoff_requests_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.cluster_handoff_requests_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: cluster_handoff_requests_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.cluster_handoff_requests_id_seq OWNED BY public.cluster_handoff_requests.id;


--
-- Name: cluster_leases; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cluster_leases (
    lease_key character varying(255) NOT NULL,
    lease_type character varying(64) NOT NULL,
    owner_worker_id character varying(100),
    acquired_at timestamp with time zone,
    renewed_at timestamp with time zone,
    expires_at timestamp with time zone,
    generation bigint DEFAULT '0'::bigint NOT NULL,
    metadata json,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);


--
-- Name: cluster_operator_destinations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cluster_operator_destinations (
    id bigint NOT NULL,
    destination_type character varying(16) NOT NULL,
    discord_user_id bigint,
    discord_guild_id bigint,
    discord_channel_id bigint,
    user_name character varying(255),
    guild_name character varying(255),
    channel_name character varying(255),
    description character varying(255),
    enabled boolean DEFAULT true NOT NULL,
    is_primary boolean DEFAULT false NOT NULL,
    last_success_at timestamp with time zone,
    last_failure_at timestamp with time zone,
    last_failure_reason character varying(255),
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);


--
-- Name: cluster_operator_destinations_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.cluster_operator_destinations_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: cluster_operator_destinations_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.cluster_operator_destinations_id_seq OWNED BY public.cluster_operator_destinations.id;


--
-- Name: cluster_operator_event_deliveries; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cluster_operator_event_deliveries (
    id bigint NOT NULL,
    event_id bigint NOT NULL,
    destination_id bigint NOT NULL,
    status character varying(16) DEFAULT 'pending'::character varying NOT NULL,
    attempt_count integer DEFAULT 0 NOT NULL,
    next_attempt_at timestamp with time zone,
    last_attempt_at timestamp with time zone,
    delivered_at timestamp with time zone,
    last_error character varying(255),
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);


--
-- Name: cluster_operator_event_deliveries_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.cluster_operator_event_deliveries_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: cluster_operator_event_deliveries_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.cluster_operator_event_deliveries_id_seq OWNED BY public.cluster_operator_event_deliveries.id;


--
-- Name: cluster_operator_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cluster_operator_events (
    id bigint NOT NULL,
    event_key character varying(255) NOT NULL,
    event_type character varying(64) NOT NULL,
    severity character varying(16) NOT NULL,
    active boolean DEFAULT true NOT NULL,
    worker_id character varying(100),
    reason character varying(255),
    message text NOT NULL,
    first_seen_at timestamp with time zone NOT NULL,
    last_seen_at timestamp with time zone NOT NULL,
    resolved_at timestamp with time zone,
    notified_at timestamp with time zone,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);


--
-- Name: cluster_operator_events_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.cluster_operator_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: cluster_operator_events_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.cluster_operator_events_id_seq OWNED BY public.cluster_operator_events.id;


--
-- Name: cluster_runtime_settings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cluster_runtime_settings (
    id bigint NOT NULL,
    setting_key character varying(150) NOT NULL,
    scope_type character varying(16) NOT NULL,
    scope_name character varying(64) DEFAULT ''::character varying NOT NULL,
    setting_value text NOT NULL,
    value_type character varying(32) NOT NULL,
    description text,
    updated_by character varying(255),
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);


--
-- Name: cluster_runtime_settings_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.cluster_runtime_settings_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: cluster_runtime_settings_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.cluster_runtime_settings_id_seq OWNED BY public.cluster_runtime_settings.id;


--
-- Name: cluster_worker_capabilities; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cluster_worker_capabilities (
    worker_id character varying(100) NOT NULL,
    capability_name character varying(64) NOT NULL,
    available boolean DEFAULT false NOT NULL,
    reason character varying(128),
    checked_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);


--
-- Name: cluster_worker_roles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cluster_worker_roles (
    worker_id character varying(100) NOT NULL,
    role_name character varying(64) NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    priority integer DEFAULT 100 NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);


--
-- Name: cluster_workers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cluster_workers (
    worker_id character varying(100) NOT NULL,
    hostname character varying(255) NOT NULL,
    site_code character varying(16) NOT NULL,
    ip_address character varying(45),
    app_version character varying(50),
    enabled boolean DEFAULT true NOT NULL,
    draining boolean DEFAULT false NOT NULL,
    status character varying(32) DEFAULT 'starting'::character varying NOT NULL,
    started_at timestamp with time zone,
    last_heartbeat_at timestamp with time zone,
    last_role_change_at timestamp with time zone,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);


--
-- Name: command_audit; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.command_audit (
    id bigint NOT NULL,
    created_at timestamp with time zone NOT NULL,
    guild_id bigint,
    guild_name character varying(255),
    channel_id bigint,
    channel_name character varying(255),
    user_id bigint,
    user_name character varying(255),
    command_name character varying(100) NOT NULL,
    command_type character varying(20) NOT NULL,
    target_type character varying(50),
    target_id character varying(255),
    target_name character varying(255),
    success boolean NOT NULL,
    result_code character varying(100),
    error_type character varying(100),
    duration_ms integer,
    request_metadata json
);


--
-- Name: command_audit_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.command_audit_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: command_audit_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.command_audit_id_seq OWNED BY public.command_audit.id;


--
-- Name: guild_announcement_channels; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.guild_announcement_channels (
    guild_id bigint NOT NULL,
    guild_name character varying(255),
    channel_id bigint NOT NULL,
    channel_name character varying(255)
);


--
-- Name: guild_listen_channels; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.guild_listen_channels (
    guild_id bigint NOT NULL,
    guild_name character varying(255),
    channel_id bigint NOT NULL,
    channel_name character varying(255)
);


--
-- Name: guild_map_role_pings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.guild_map_role_pings (
    guild_id bigint NOT NULL,
    guild_name character varying(255),
    map_key character varying(100) NOT NULL,
    map_name character varying(255),
    role_id bigint NOT NULL,
    role_name character varying(255),
    message text NOT NULL
);


--
-- Name: guild_player_watch_alerts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.guild_player_watch_alerts (
    watch_id bigint NOT NULL,
    session_id bigint NOT NULL,
    alerted_at timestamp with time zone NOT NULL
);


--
-- Name: guild_player_watches; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.guild_player_watches (
    id bigint NOT NULL,
    guild_id bigint NOT NULL,
    watched_name character varying(255) NOT NULL,
    normalized_name character varying(255) NOT NULL,
    persona_id bigint,
    created_by_user_id bigint NOT NULL,
    created_at timestamp with time zone NOT NULL,
    platform character varying(32) NOT NULL
);


--
-- Name: guild_player_watches_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.guild_player_watches_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: guild_player_watches_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.guild_player_watches_id_seq OWNED BY public.guild_player_watches.id;


--
-- Name: guild_role_panel_messages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.guild_role_panel_messages (
    guild_id bigint NOT NULL,
    guild_name character varying(255),
    panel_index integer NOT NULL,
    channel_id bigint NOT NULL,
    channel_name character varying(255),
    message_id bigint NOT NULL
);


--
-- Name: guild_server_player_messages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.guild_server_player_messages (
    guild_id bigint NOT NULL,
    guild_name character varying(255),
    server_guid character varying(36) NOT NULL,
    server_name character varying(255),
    chunk_index integer NOT NULL,
    channel_id bigint NOT NULL,
    channel_name character varying(255),
    message_id bigint NOT NULL,
    content_hash character varying(64) NOT NULL
);


--
-- Name: guild_server_state; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.guild_server_state (
    guild_id bigint NOT NULL,
    guild_name character varying(255),
    server_guid character varying(36) NOT NULL,
    last_map_key character varying(100),
    last_map_name character varying(255),
    announcement_channel_id bigint,
    announcement_channel_name character varying(255),
    announcement_message_id bigint,
    player_eta_channel_id bigint,
    player_eta_channel_name character varying(255),
    player_eta_message_id bigint
);


--
-- Name: guild_servers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.guild_servers (
    guild_id bigint NOT NULL,
    server_guid character varying(36) NOT NULL,
    display_name character varying(255) NOT NULL,
    is_default boolean DEFAULT false NOT NULL,
    include_users boolean DEFAULT false NOT NULL,
    announcement_channel_id bigint,
    announcement_channel_name character varying(255)
);


--
-- Name: guild_settings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.guild_settings (
    guild_id bigint NOT NULL,
    guild_name character varying(255),
    management_min_role_id bigint NOT NULL,
    management_min_role_name character varying(255),
    status_min_role_id bigint NOT NULL,
    status_min_role_name character varying(255),
    roles_channel_id bigint DEFAULT '0'::bigint NOT NULL,
    roles_channel_name character varying(255),
    watched_player_channel_id bigint NOT NULL,
    watched_player_channel_name character varying(255)
);


--
-- Name: guilds; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.guilds (
    guild_id bigint NOT NULL,
    guild_name character varying(255) NOT NULL,
    joined_at timestamp with time zone NOT NULL,
    left_at timestamp with time zone
);


--
-- Name: keeper_rate_gate; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.keeper_rate_gate (
    gate_key character varying(64) NOT NULL,
    next_request_at timestamp with time zone NOT NULL,
    last_worker_id character varying(100),
    total_grants bigint DEFAULT '0'::bigint NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);


--
-- Name: keeper_snapshots; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.keeper_snapshots (
    server_guid character varying(36) NOT NULL,
    snapshot json NOT NULL,
    fetched_at timestamp with time zone NOT NULL,
    worker_id character varying(100) NOT NULL,
    fetch_generation bigint DEFAULT '0'::bigint NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);


--
-- Name: player_persona_enrichment_state; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.player_persona_enrichment_state (
    server_guid character varying(36) NOT NULL,
    retry_after timestamp with time zone,
    no_progress_streak integer DEFAULT 0 NOT NULL,
    last_attempt_at timestamp with time zone,
    last_progress_at timestamp with time zone,
    last_result character varying(64),
    last_error_type character varying(100),
    last_error_message text,
    claim_worker_id character varying(100),
    claim_started_at timestamp with time zone,
    claim_expires_at timestamp with time zone,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);


--
-- Name: bf4_player_aliases id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bf4_player_aliases ALTER COLUMN id SET DEFAULT nextval('public.bf4_player_aliases_id_seq'::regclass);


--
-- Name: bf4_player_sessions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bf4_player_sessions ALTER COLUMN id SET DEFAULT nextval('public.bf4_player_sessions_id_seq'::regclass);


--
-- Name: cluster_handoff_requests id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_handoff_requests ALTER COLUMN id SET DEFAULT nextval('public.cluster_handoff_requests_id_seq'::regclass);


--
-- Name: cluster_operator_destinations id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_operator_destinations ALTER COLUMN id SET DEFAULT nextval('public.cluster_operator_destinations_id_seq'::regclass);


--
-- Name: cluster_operator_event_deliveries id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_operator_event_deliveries ALTER COLUMN id SET DEFAULT nextval('public.cluster_operator_event_deliveries_id_seq'::regclass);


--
-- Name: cluster_operator_events id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_operator_events ALTER COLUMN id SET DEFAULT nextval('public.cluster_operator_events_id_seq'::regclass);


--
-- Name: cluster_runtime_settings id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_runtime_settings ALTER COLUMN id SET DEFAULT nextval('public.cluster_runtime_settings_id_seq'::regclass);


--
-- Name: command_audit id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.command_audit ALTER COLUMN id SET DEFAULT nextval('public.command_audit_id_seq'::regclass);


--
-- Name: guild_player_watches id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.guild_player_watches ALTER COLUMN id SET DEFAULT nextval('public.guild_player_watches_id_seq'::regclass);


--
-- Name: alembic_version alembic_version_pkc; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alembic_version
    ADD CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num);


--
-- Name: bf4_maps bf4_maps_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bf4_maps
    ADD CONSTRAINT bf4_maps_pkey PRIMARY KEY (map_key);


--
-- Name: bf4_player_aliases bf4_player_aliases_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bf4_player_aliases
    ADD CONSTRAINT bf4_player_aliases_pkey PRIMARY KEY (id);


--
-- Name: bf4_player_sessions bf4_player_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bf4_player_sessions
    ADD CONSTRAINT bf4_player_sessions_pkey PRIMARY KEY (id);


--
-- Name: bf4_servers bf4_servers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bf4_servers
    ADD CONSTRAINT bf4_servers_pkey PRIMARY KEY (server_guid);


--
-- Name: cluster_handoff_requests cluster_handoff_requests_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_handoff_requests
    ADD CONSTRAINT cluster_handoff_requests_pkey PRIMARY KEY (id);


--
-- Name: cluster_leases cluster_leases_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_leases
    ADD CONSTRAINT cluster_leases_pkey PRIMARY KEY (lease_key);


--
-- Name: cluster_operator_destinations cluster_operator_destinations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_operator_destinations
    ADD CONSTRAINT cluster_operator_destinations_pkey PRIMARY KEY (id);


--
-- Name: cluster_operator_event_deliveries cluster_operator_event_deliveries_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_operator_event_deliveries
    ADD CONSTRAINT cluster_operator_event_deliveries_pkey PRIMARY KEY (id);


--
-- Name: cluster_operator_events cluster_operator_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_operator_events
    ADD CONSTRAINT cluster_operator_events_pkey PRIMARY KEY (id);


--
-- Name: cluster_runtime_settings cluster_runtime_settings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_runtime_settings
    ADD CONSTRAINT cluster_runtime_settings_pkey PRIMARY KEY (id);


--
-- Name: cluster_worker_capabilities cluster_worker_capabilities_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_worker_capabilities
    ADD CONSTRAINT cluster_worker_capabilities_pkey PRIMARY KEY (worker_id, capability_name);


--
-- Name: cluster_worker_roles cluster_worker_roles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_worker_roles
    ADD CONSTRAINT cluster_worker_roles_pkey PRIMARY KEY (worker_id, role_name);


--
-- Name: cluster_workers cluster_workers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_workers
    ADD CONSTRAINT cluster_workers_pkey PRIMARY KEY (worker_id);


--
-- Name: command_audit command_audit_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.command_audit
    ADD CONSTRAINT command_audit_pkey PRIMARY KEY (id);


--
-- Name: guild_announcement_channels guild_announcement_channels_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.guild_announcement_channels
    ADD CONSTRAINT guild_announcement_channels_pkey PRIMARY KEY (guild_id, channel_id);


--
-- Name: guild_listen_channels guild_listen_channels_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.guild_listen_channels
    ADD CONSTRAINT guild_listen_channels_pkey PRIMARY KEY (guild_id, channel_id);


--
-- Name: guild_map_role_pings guild_map_role_pings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.guild_map_role_pings
    ADD CONSTRAINT guild_map_role_pings_pkey PRIMARY KEY (guild_id, map_key);


--
-- Name: guild_player_watch_alerts guild_player_watch_alerts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.guild_player_watch_alerts
    ADD CONSTRAINT guild_player_watch_alerts_pkey PRIMARY KEY (watch_id, session_id);


--
-- Name: guild_player_watches guild_player_watches_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.guild_player_watches
    ADD CONSTRAINT guild_player_watches_pkey PRIMARY KEY (id);


--
-- Name: guild_role_panel_messages guild_role_panel_messages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.guild_role_panel_messages
    ADD CONSTRAINT guild_role_panel_messages_pkey PRIMARY KEY (guild_id, panel_index);


--
-- Name: guild_server_player_messages guild_server_player_messages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.guild_server_player_messages
    ADD CONSTRAINT guild_server_player_messages_pkey PRIMARY KEY (guild_id, server_guid, chunk_index);


--
-- Name: guild_server_state guild_server_state_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.guild_server_state
    ADD CONSTRAINT guild_server_state_pkey PRIMARY KEY (guild_id, server_guid);


--
-- Name: guild_servers guild_servers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.guild_servers
    ADD CONSTRAINT guild_servers_pkey PRIMARY KEY (guild_id, server_guid);


--
-- Name: guild_settings guild_settings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.guild_settings
    ADD CONSTRAINT guild_settings_pkey PRIMARY KEY (guild_id);


--
-- Name: guilds guilds_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.guilds
    ADD CONSTRAINT guilds_pkey PRIMARY KEY (guild_id);


--
-- Name: keeper_rate_gate keeper_rate_gate_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.keeper_rate_gate
    ADD CONSTRAINT keeper_rate_gate_pkey PRIMARY KEY (gate_key);


--
-- Name: keeper_snapshots keeper_snapshots_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.keeper_snapshots
    ADD CONSTRAINT keeper_snapshots_pkey PRIMARY KEY (server_guid);


--
-- Name: player_persona_enrichment_state player_persona_enrichment_state_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.player_persona_enrichment_state
    ADD CONSTRAINT player_persona_enrichment_state_pkey PRIMARY KEY (server_guid);


--
-- Name: bf4_player_aliases uq_bf4_player_alias_identity_name; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bf4_player_aliases
    ADD CONSTRAINT uq_bf4_player_alias_identity_name UNIQUE (platform, persona_id, normalized_name);


--
-- Name: cluster_operator_event_deliveries uq_cluster_operator_event_delivery; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_operator_event_deliveries
    ADD CONSTRAINT uq_cluster_operator_event_delivery UNIQUE (event_id, destination_id);


--
-- Name: cluster_runtime_settings uq_cluster_runtime_setting_scope; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_runtime_settings
    ADD CONSTRAINT uq_cluster_runtime_setting_scope UNIQUE (setting_key, scope_type, scope_name);


--
-- Name: guild_player_watches uq_guild_player_watch_platform_name; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.guild_player_watches
    ADD CONSTRAINT uq_guild_player_watch_platform_name UNIQUE (guild_id, platform, normalized_name);


--
-- Name: ix_bf4_player_aliases_identity; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_bf4_player_aliases_identity ON public.bf4_player_aliases USING btree (platform, persona_id);


--
-- Name: ix_bf4_player_aliases_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_bf4_player_aliases_name ON public.bf4_player_aliases USING btree (normalized_name);


--
-- Name: ix_bf4_player_sessions_normalized_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_bf4_player_sessions_normalized_name ON public.bf4_player_sessions USING btree (normalized_name);


--
-- Name: ix_bf4_player_sessions_open_unresolved_persona; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_bf4_player_sessions_open_unresolved_persona ON public.bf4_player_sessions USING btree (server_guid, time_left, persona_id);


--
-- Name: ix_bf4_player_sessions_persona_alert_mode; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_bf4_player_sessions_persona_alert_mode ON public.bf4_player_sessions USING btree (persona_alert_mode);


--
-- Name: ix_bf4_player_sessions_persona_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_bf4_player_sessions_persona_id ON public.bf4_player_sessions USING btree (persona_id);


--
-- Name: ix_bf4_player_sessions_server_open; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_bf4_player_sessions_server_open ON public.bf4_player_sessions USING btree (server_guid, time_left);


--
-- Name: ix_bf4_player_sessions_time_joined; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_bf4_player_sessions_time_joined ON public.bf4_player_sessions USING btree (time_joined);


--
-- Name: ix_cluster_handoff_requests_lease_key; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_cluster_handoff_requests_lease_key ON public.cluster_handoff_requests USING btree (lease_key);


--
-- Name: ix_cluster_handoff_requests_status_expires; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_cluster_handoff_requests_status_expires ON public.cluster_handoff_requests USING btree (status, expires_at);


--
-- Name: ix_cluster_handoff_requests_target; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_cluster_handoff_requests_target ON public.cluster_handoff_requests USING btree (target_worker_id);


--
-- Name: ix_cluster_leases_expires; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_cluster_leases_expires ON public.cluster_leases USING btree (expires_at);


--
-- Name: ix_cluster_leases_owner; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_cluster_leases_owner ON public.cluster_leases USING btree (owner_worker_id);


--
-- Name: ix_cluster_leases_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_cluster_leases_type ON public.cluster_leases USING btree (lease_type);


--
-- Name: ix_cluster_operator_events_delivery; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_cluster_operator_events_delivery ON public.cluster_operator_events USING btree (notified_at, created_at);


--
-- Name: ix_cluster_operator_events_key_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_cluster_operator_events_key_active ON public.cluster_operator_events USING btree (event_key, active);


--
-- Name: ix_cluster_runtime_settings_key; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_cluster_runtime_settings_key ON public.cluster_runtime_settings USING btree (setting_key);


--
-- Name: ix_cluster_runtime_settings_scope; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_cluster_runtime_settings_scope ON public.cluster_runtime_settings USING btree (scope_type, scope_name);


--
-- Name: ix_cluster_worker_capabilities_name_available; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_cluster_worker_capabilities_name_available ON public.cluster_worker_capabilities USING btree (capability_name, available);


--
-- Name: ix_cluster_worker_roles_role_enabled; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_cluster_worker_roles_role_enabled ON public.cluster_worker_roles USING btree (role_name, enabled);


--
-- Name: ix_cluster_workers_enabled_draining; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_cluster_workers_enabled_draining ON public.cluster_workers USING btree (enabled, draining);


--
-- Name: ix_cluster_workers_last_heartbeat; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_cluster_workers_last_heartbeat ON public.cluster_workers USING btree (last_heartbeat_at);


--
-- Name: ix_cluster_workers_site; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_cluster_workers_site ON public.cluster_workers USING btree (site_code);


--
-- Name: ix_command_audit_command_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_command_audit_command_created ON public.command_audit USING btree (command_name, created_at);


--
-- Name: ix_command_audit_guild_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_command_audit_guild_created ON public.command_audit USING btree (guild_id, created_at);


--
-- Name: ix_command_audit_success_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_command_audit_success_created ON public.command_audit USING btree (success, created_at);


--
-- Name: ix_command_audit_user_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_command_audit_user_created ON public.command_audit USING btree (user_id, created_at);


--
-- Name: ix_guild_player_watches_guild_platform; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_guild_player_watches_guild_platform ON public.guild_player_watches USING btree (guild_id, platform);


--
-- Name: ix_guild_player_watches_persona_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_guild_player_watches_persona_id ON public.guild_player_watches USING btree (persona_id);


--
-- Name: ix_keeper_snapshots_fetched_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_keeper_snapshots_fetched_at ON public.keeper_snapshots USING btree (fetched_at);


--
-- Name: ix_operator_delivery_due; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_operator_delivery_due ON public.cluster_operator_event_deliveries USING btree (status, next_attempt_at);


--
-- Name: ix_operator_dest_channel; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_operator_dest_channel ON public.cluster_operator_destinations USING btree (destination_type, discord_guild_id, discord_channel_id) WHERE ((destination_type)::text = 'channel'::text);


--
-- Name: ix_operator_dest_user; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_operator_dest_user ON public.cluster_operator_destinations USING btree (destination_type, discord_user_id) WHERE ((destination_type)::text = 'dm'::text);


--
-- Name: ix_player_persona_enrichment_claim_expires_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_player_persona_enrichment_claim_expires_at ON public.player_persona_enrichment_state USING btree (claim_expires_at);


--
-- Name: ix_player_persona_enrichment_retry_after; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_player_persona_enrichment_retry_after ON public.player_persona_enrichment_state USING btree (retry_after);


--
-- Name: bf4_player_sessions bf4_player_sessions_server_guid_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bf4_player_sessions
    ADD CONSTRAINT bf4_player_sessions_server_guid_fkey FOREIGN KEY (server_guid) REFERENCES public.bf4_servers(server_guid);


--
-- Name: cluster_handoff_requests cluster_handoff_requests_source_worker_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_handoff_requests
    ADD CONSTRAINT cluster_handoff_requests_source_worker_id_fkey FOREIGN KEY (source_worker_id) REFERENCES public.cluster_workers(worker_id) ON DELETE SET NULL;


--
-- Name: cluster_handoff_requests cluster_handoff_requests_target_worker_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_handoff_requests
    ADD CONSTRAINT cluster_handoff_requests_target_worker_id_fkey FOREIGN KEY (target_worker_id) REFERENCES public.cluster_workers(worker_id) ON DELETE SET NULL;


--
-- Name: cluster_leases cluster_leases_owner_worker_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_leases
    ADD CONSTRAINT cluster_leases_owner_worker_id_fkey FOREIGN KEY (owner_worker_id) REFERENCES public.cluster_workers(worker_id) ON DELETE SET NULL;


--
-- Name: cluster_operator_event_deliveries cluster_operator_event_deliveries_destination_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_operator_event_deliveries
    ADD CONSTRAINT cluster_operator_event_deliveries_destination_id_fkey FOREIGN KEY (destination_id) REFERENCES public.cluster_operator_destinations(id) ON DELETE CASCADE;


--
-- Name: cluster_operator_event_deliveries cluster_operator_event_deliveries_event_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_operator_event_deliveries
    ADD CONSTRAINT cluster_operator_event_deliveries_event_id_fkey FOREIGN KEY (event_id) REFERENCES public.cluster_operator_events(id) ON DELETE CASCADE;


--
-- Name: cluster_operator_events cluster_operator_events_worker_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_operator_events
    ADD CONSTRAINT cluster_operator_events_worker_id_fkey FOREIGN KEY (worker_id) REFERENCES public.cluster_workers(worker_id) ON DELETE SET NULL;


--
-- Name: cluster_worker_capabilities cluster_worker_capabilities_worker_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_worker_capabilities
    ADD CONSTRAINT cluster_worker_capabilities_worker_id_fkey FOREIGN KEY (worker_id) REFERENCES public.cluster_workers(worker_id) ON DELETE CASCADE;


--
-- Name: cluster_worker_roles cluster_worker_roles_worker_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_worker_roles
    ADD CONSTRAINT cluster_worker_roles_worker_id_fkey FOREIGN KEY (worker_id) REFERENCES public.cluster_workers(worker_id) ON DELETE CASCADE;


--
-- Name: guild_announcement_channels guild_announcement_channels_guild_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.guild_announcement_channels
    ADD CONSTRAINT guild_announcement_channels_guild_id_fkey FOREIGN KEY (guild_id) REFERENCES public.guilds(guild_id) ON DELETE CASCADE;


--
-- Name: guild_listen_channels guild_listen_channels_guild_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.guild_listen_channels
    ADD CONSTRAINT guild_listen_channels_guild_id_fkey FOREIGN KEY (guild_id) REFERENCES public.guilds(guild_id) ON DELETE CASCADE;


--
-- Name: guild_map_role_pings guild_map_role_pings_guild_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.guild_map_role_pings
    ADD CONSTRAINT guild_map_role_pings_guild_id_fkey FOREIGN KEY (guild_id) REFERENCES public.guilds(guild_id) ON DELETE CASCADE;


--
-- Name: guild_map_role_pings guild_map_role_pings_map_key_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.guild_map_role_pings
    ADD CONSTRAINT guild_map_role_pings_map_key_fkey FOREIGN KEY (map_key) REFERENCES public.bf4_maps(map_key);


--
-- Name: guild_player_watch_alerts guild_player_watch_alerts_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.guild_player_watch_alerts
    ADD CONSTRAINT guild_player_watch_alerts_session_id_fkey FOREIGN KEY (session_id) REFERENCES public.bf4_player_sessions(id) ON DELETE CASCADE;


--
-- Name: guild_player_watch_alerts guild_player_watch_alerts_watch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.guild_player_watch_alerts
    ADD CONSTRAINT guild_player_watch_alerts_watch_id_fkey FOREIGN KEY (watch_id) REFERENCES public.guild_player_watches(id) ON DELETE CASCADE;


--
-- Name: guild_player_watches guild_player_watches_guild_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.guild_player_watches
    ADD CONSTRAINT guild_player_watches_guild_id_fkey FOREIGN KEY (guild_id) REFERENCES public.guilds(guild_id) ON DELETE CASCADE;


--
-- Name: guild_role_panel_messages guild_role_panel_messages_guild_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.guild_role_panel_messages
    ADD CONSTRAINT guild_role_panel_messages_guild_id_fkey FOREIGN KEY (guild_id) REFERENCES public.guilds(guild_id) ON DELETE CASCADE;


--
-- Name: guild_server_player_messages guild_server_player_messages_guild_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.guild_server_player_messages
    ADD CONSTRAINT guild_server_player_messages_guild_id_fkey FOREIGN KEY (guild_id) REFERENCES public.guilds(guild_id) ON DELETE CASCADE;


--
-- Name: guild_server_player_messages guild_server_player_messages_server_guid_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.guild_server_player_messages
    ADD CONSTRAINT guild_server_player_messages_server_guid_fkey FOREIGN KEY (server_guid) REFERENCES public.bf4_servers(server_guid);


--
-- Name: guild_server_state guild_server_state_guild_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.guild_server_state
    ADD CONSTRAINT guild_server_state_guild_id_fkey FOREIGN KEY (guild_id) REFERENCES public.guilds(guild_id) ON DELETE CASCADE;


--
-- Name: guild_server_state guild_server_state_server_guid_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.guild_server_state
    ADD CONSTRAINT guild_server_state_server_guid_fkey FOREIGN KEY (server_guid) REFERENCES public.bf4_servers(server_guid);


--
-- Name: guild_servers guild_servers_guild_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.guild_servers
    ADD CONSTRAINT guild_servers_guild_id_fkey FOREIGN KEY (guild_id) REFERENCES public.guilds(guild_id) ON DELETE CASCADE;


--
-- Name: guild_servers guild_servers_server_guid_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.guild_servers
    ADD CONSTRAINT guild_servers_server_guid_fkey FOREIGN KEY (server_guid) REFERENCES public.bf4_servers(server_guid);


--
-- Name: guild_settings guild_settings_guild_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.guild_settings
    ADD CONSTRAINT guild_settings_guild_id_fkey FOREIGN KEY (guild_id) REFERENCES public.guilds(guild_id) ON DELETE CASCADE;


--
-- Name: keeper_snapshots keeper_snapshots_server_guid_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.keeper_snapshots
    ADD CONSTRAINT keeper_snapshots_server_guid_fkey FOREIGN KEY (server_guid) REFERENCES public.bf4_servers(server_guid) ON DELETE CASCADE;


--
-- Name: player_persona_enrichment_state player_persona_enrichment_state_claim_worker_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.player_persona_enrichment_state
    ADD CONSTRAINT player_persona_enrichment_state_claim_worker_id_fkey FOREIGN KEY (claim_worker_id) REFERENCES public.cluster_workers(worker_id) ON DELETE SET NULL;


--
-- Name: player_persona_enrichment_state player_persona_enrichment_state_server_guid_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.player_persona_enrichment_state
    ADD CONSTRAINT player_persona_enrichment_state_server_guid_fkey FOREIGN KEY (server_guid) REFERENCES public.bf4_servers(server_guid) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--

\unrestrict dU6v6wPezc1lorrrc0s19cxAaYjmrfPLW2aVQAJnQmofJYSLOLy2ZVxwQd7m6Xv

