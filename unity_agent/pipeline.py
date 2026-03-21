"""Main autoformalization pipeline for Unity Agent."""

import os
import sys
import logging
import subprocess
from pathlib import Path

from dotenv import load_dotenv
from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, ResultMessage, AgentDefinition


def _run(cmd, cwd=None):
    subprocess.run(cmd, cwd=cwd, check=True)


def _is_lean_repo(path: Path) -> bool:
    return (path / "lean-toolchain").exists() and (
        (path / "lakefile.lean").exists() or (path / "lakefile.toml").exists()
    )


def _get_prompts_dir() -> Path:
    """Get the PROMPTS directory relative to this package."""
    return Path(__file__).parent / "PROMPTS"


def _get_subagents_dir() -> Path:
    """Get the SUBAGENTS directory relative to this package."""
    return Path(__file__).parent / "SUBAGENTS"


async def run_pipeline(source: str, project_dir: str, context: bool):
    """Run the full autoformalization pipeline."""
    PROJECT_DIR = project_dir
    logging.basicConfig(level=logging.DEBUG)

    # Helper functions for environment variable parsing
    def parse_bool(val: str | None) -> bool:
        """Parse string env var to boolean."""
        return val is not None and val.lower() in ("true", "1", "yes")
    
    def parse_float(val: str | None) -> float | None:
        """Parse string env var to float, or None if not set/empty."""
        if not val or val.lower() == "none":
            return None
        return float(val)

    # Load environment
    try:
        load_dotenv()
        silent = parse_bool(os.getenv("SILENT"))
        
        # Reroute output if needed
        if silent:
            sys.stdout = open("unity.out", 'w')
            sys.stderr = open("unity.err", 'w')
        
        logging.info("Loading environment...")
        
        # Set environment
        save_spec = parse_bool(os.getenv("SAVE_SPECIFICATION"))
        no_bypass = parse_bool(os.getenv("NO_BYPASS"))
        generation_budget = parse_float(os.getenv("GENERATION_BUDGET"))
        semiformalization_budget = parse_float(os.getenv("SEMIFORMALIZATION_BUDGET"))
        exploration_budget = parse_float(os.getenv("EXPLORATION_BUDGET"))
        preparation_budget = parse_float(os.getenv("PREPARATION_BUDGET"))
        formalization_budget = parse_float(os.getenv("FORMALIZATION_BUDGET"))
        critic_budget = parse_float(os.getenv("CRITIC_BUDGET"))
        save_semiformalization = parse_bool(os.getenv("SAVE_SEMIFORMALIZATION"))
        autofix = parse_bool(os.getenv("AUTOFIX"))
        exploration = parse_bool(os.getenv("EXPLORATION"))
        recurse = parse_bool(os.getenv("RECURSE"))
        
        
        # Check for conflicts
        if not autofix and context:
            logging.critical("CRITICAL: cannot have context without autofix enabled")
            exit(1)
            
        if not exploration and recurse:
            logging.critical("CRITICAL: cannot have recurse without exploration enabled")
            exit(1)

        # Set permissions
        
        if no_bypass:
            PERMISSIONS="acceptEdits"
        else:
            PERMISSIONS="bypassPermissions"
        
        logging.info("Environment loaded successfully!")
    except Exception as e:
        logging.critical(f"CRITICAL (environment loading): {e}")
        exit(1)
        
    # Lean project initialization

    logging.info("Initializing Lean project...")
    try:
        # Create Lean project (if needed), get cache, and update
        project_path = Path(PROJECT_DIR).expanduser().resolve()

        if not project_path.exists():
            project_path.parent.mkdir(parents=True, exist_ok=True)
            _run(["lake", "new", project_path.name, "math"], cwd=project_path.parent)
        elif not _is_lean_repo(project_path):
            _run(["lake", "init", project_path.name, "math"], cwd=project_path)

        _run(["lake", "exe", "cache", "get"], cwd=project_path)
        _run(["lake", "update"], cwd=project_path)
        
        logging.info("Lean project initialized successfully!")
    except Exception as e:
        logging.critical(f"CRITICAL (project initialization): {e}")
        exit(1)

    # Configure lean-lsp-mcp server for all agents
    LEAN_MCP_SERVER = {
        "lean-lsp": {
            "command": "uvx",
            "args": ["lean-lsp-mcp"],
            "cwd": str(project_path)
        }
    }

    # Generation phase
    
    logging.info("Beginning generation phase...")
    try:
        # Load generation phase system prompt and generator subagent prompt
        with open(_get_prompts_dir() / "GENERATION.md", "r") as f:
            GENERATION_PROMPT = f.read()
        with open(_get_subagents_dir() / "GENERATION/GENERATOR.md", "r") as f:
            GENERATOR_SUBAGENT = f.read()
        
        async for message in query(
            prompt=f"Generate the specification language for {source}.",
            options=ClaudeAgentOptions(
                tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebSearch", "WebFetch", "Agent", "Skill"],
                allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebSearch", "WebFetch", "Agent", "Skill"],
                agents={
                    "generator": AgentDefinition(
                        description="Generator subagent. Capable of assisting in the design of a semiformal specification language for a given source.",
                        prompt=GENERATOR_SUBAGENT,
                        tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebSearch", "WebFetch", "Agent", "Skill"]
                    )
                },
                system_prompt=GENERATION_PROMPT,
                mcp_servers=LEAN_MCP_SERVER,
                permission_mode=PERMISSIONS,
                continue_conversation=False,
                max_budget_usd=generation_budget,
                disallowed_tools=[],
                enable_file_checkpointing=True,
                model="opus",
                fallback_model="sonnet",
                env={
                    "ANTHROPIC_BASE_URL":os.getenv("ANTHROPIC_BASE_URL"),
                    "ANTHROPIC_API_KEY":os.getenv("ANTHROPIC_API_KEY"),
                    "ANTHROPIC_AUTH_TOKEN":os.getenv("ANTHROPIC_AUTH_TOKEN"),
                    "ANTHROPIC_DEFAULT_OPUS_MODEL":os.getenv("ANTHROPIC_DEFAULT_OPUS_MODEL"),
                    "ANTHROPIC_DEFAULT_SONNET_MODEL":os.getenv("ANTHROPIC_DEFAULT_SONNET_MODEL"),
                    "ANTHROPIC_DEFAULT_HAIKU_MODEL":os.getenv("ANTHROPIC_DEFAULT_HAIKU_MODEL"),
                    "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS":os.getenv("CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS")
                },
                extra_args={},
            ),
        ):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if hasattr(block, "text"):
                        print(block.text)
                    elif hasattr(block, "name"):
                        print(f"Tool: {block.name}")
            elif isinstance(message, ResultMessage):
                print(f"Done: {message.subtype}")
                
        logging.info("Generation phase completed successfully!")

        exit(0)
    except Exception as e:
        logging.critical(f"CRITICAL (generation phase): {e}")
        exit(1)
        
    # Semiformalization phase
    
    logging.info("Beginning semiformalization phase...")
    if not autofix and not context:
        try:
            # Load semiformalization phase system prompt and semiformalizer subagent prompt
            with open(_get_prompts_dir() / "SEMIFORMALIZATION/FF.md", "r") as f:
                SEMIFORMALIZATION_PROMPT = f.read()
            with open(_get_subagents_dir() / "SEMIFORMALIZATION/FF.md", "r") as f:
                SEMIFORMALIZER_SUBAGENT = f.read()
            
            async for message in query(
                prompt=f"Semiformalize {source} as specified by the language.",
                options=ClaudeAgentOptions(
                    tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebSearch", "WebFetch", "Agent", "Skill"],
                    allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebSearch", "WebFetch", "Agent", "Skill"],
                    agents={
                        "semiformalizer": AgentDefinition(
                            description="Semiformalizer subagent. Capable of producing faithful semiformal translations of a source into the IR specification language located in `language/`.",
                            prompt=SEMIFORMALIZER_SUBAGENT,
                            tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebSearch", "WebFetch", "Agent", "Skill"]
                        )
                    },
                    system_prompt=SEMIFORMALIZATION_PROMPT,
                    mcp_servers=LEAN_MCP_SERVER,
                    permission_mode=PERMISSIONS,
                    continue_conversation=False,
                    max_budget_usd=semiformalization_budget,
                    disallowed_tools=[],
                    enable_file_checkpointing=True,
                    model="opus",
                    fallback_model="sonnet",
                    env={
                        "ANTHROPIC_BASE_URL":os.getenv("ANTHROPIC_BASE_URL"),
                        "ANTHROPIC_API_KEY":os.getenv("ANTHROPIC_API_KEY"),
                        "ANTHROPIC_AUTH_TOKEN":os.getenv("ANTHROPIC_AUTH_TOKEN"),
                        "ANTHROPIC_DEFAULT_OPUS_MODEL":os.getenv("ANTHROPIC_DEFAULT_OPUS_MODEL"),
                        "ANTHROPIC_DEFAULT_SONNET_MODEL":os.getenv("ANTHROPIC_DEFAULT_SONNET_MODEL"),
                        "ANTHROPIC_DEFAULT_HAIKU_MODEL":os.getenv("ANTHROPIC_DEFAULT_HAIKU_MODEL"),
                        "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS":os.getenv("CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS")
                    },
                    extra_args={},
                ),
            ):
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if hasattr(block, "text"):
                            print(block.text)
                        elif hasattr(block, "name"):
                            print(f"Tool: {block.name}")
                elif isinstance(message, ResultMessage):
                    print(f"Done: {message.subtype}")
                    
            logging.info("Semiformalization phase completed successfully!")
        except Exception as e:
            logging.critical(f"CRITICAL (semiformalization phase): {e}")
            exit(1)
    elif autofix and not context:
        try:
            # Load semiformalization phase system prompt and semiformalizer subagent prompt
            with open(_get_prompts_dir() / "SEMIFORMALIZATION/TF.md", "r") as f:
                SEMIFORMALIZATION_PROMPT = f.read()
            with open(_get_subagents_dir() / "SEMIFORMALIZATION/TF.md", "r") as f:
                SEMIFORMALIZER_SUBAGENT = f.read()
            
            async for message in query(
                prompt=f"Semiformalize {source} as specified by the language.",
                options=ClaudeAgentOptions(
                    tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebSearch", "WebFetch", "Agent", "Skill"],
                    allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebSearch", "WebFetch", "Agent", "Skill"],
                    agents={
                        "semiformalizer": AgentDefinition(
                            description="Semiformalizer subagent. Capable of producing faithful semiformal translations of a source into the IR specification language located in `language/`.",
                            prompt=SEMIFORMALIZER_SUBAGENT,
                            tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebSearch", "WebFetch", "Agent", "Skill"]
                        )
                    },
                    system_prompt=SEMIFORMALIZATION_PROMPT,
                    mcp_servers=LEAN_MCP_SERVER,
                    permission_mode=PERMISSIONS,
                    continue_conversation=False,
                    max_budget_usd=semiformalization_budget,
                    disallowed_tools=[],
                    enable_file_checkpointing=True,
                    model="opus",
                    fallback_model="sonnet",
                    env={
                        "ANTHROPIC_BASE_URL":os.getenv("ANTHROPIC_BASE_URL"),
                        "ANTHROPIC_API_KEY":os.getenv("ANTHROPIC_API_KEY"),
                        "ANTHROPIC_AUTH_TOKEN":os.getenv("ANTHROPIC_AUTH_TOKEN"),
                        "ANTHROPIC_DEFAULT_OPUS_MODEL":os.getenv("ANTHROPIC_DEFAULT_OPUS_MODEL"),
                        "ANTHROPIC_DEFAULT_SONNET_MODEL":os.getenv("ANTHROPIC_DEFAULT_SONNET_MODEL"),
                        "ANTHROPIC_DEFAULT_HAIKU_MODEL":os.getenv("ANTHROPIC_DEFAULT_HAIKU_MODEL"),
                        "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS":os.getenv("CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS")
                    },
                    extra_args={},
                ),
            ):
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if hasattr(block, "text"):
                            print(block.text)
                        elif hasattr(block, "name"):
                            print(f"Tool: {block.name}")
                elif isinstance(message, ResultMessage):
                    print(f"Done: {message.subtype}")
                    
            logging.info("Semiformalization phase completed successfully!")
        except Exception as e:
            logging.critical(f"CRITICAL (semiformalization phase): {e}")
            exit(1)
    elif autofix and context:
        try:
            # Load semiformalization phase system prompt and semiformalizer subagent prompt
            with open(_get_prompts_dir() / "SEMIFORMALIZATION/TT.md", "r") as f:
                SEMIFORMALIZATION_PROMPT = f.read()
            with open(_get_subagents_dir() / "SEMIFORMALIZATION/TT.md", "r") as f:
                SEMIFORMALIZER_SUBAGENT = f.read()
            
            async for message in query(
                prompt=f"Semiformalize {source} as specified by the language. The Lean project is {project_path}.",
                options=ClaudeAgentOptions(
                    tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebSearch", "WebFetch", "Agent", "Skill"],
                    allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebSearch", "WebFetch", "Agent", "Skill"],
                    agents={
                        "semiformalizer": AgentDefinition(
                            description="Semiformalizer subagent. Capable of producing faithful semiformal translations of a source into the IR specification language located in `language/`.",
                            prompt=SEMIFORMALIZER_SUBAGENT,
                            tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebSearch", "WebFetch", "Agent", "Skill"]
                        )
                    },
                    system_prompt=SEMIFORMALIZATION_PROMPT,
                    mcp_servers=LEAN_MCP_SERVER,
                    permission_mode=PERMISSIONS,
                    continue_conversation=False,
                    max_budget_usd=semiformalization_budget,
                    disallowed_tools=[],
                    enable_file_checkpointing=True,
                    model="opus",
                    fallback_model="sonnet",
                    env={
                        "ANTHROPIC_BASE_URL":os.getenv("ANTHROPIC_BASE_URL"),
                        "ANTHROPIC_API_KEY":os.getenv("ANTHROPIC_API_KEY"),
                        "ANTHROPIC_AUTH_TOKEN":os.getenv("ANTHROPIC_AUTH_TOKEN"),
                        "ANTHROPIC_DEFAULT_OPUS_MODEL":os.getenv("ANTHROPIC_DEFAULT_OPUS_MODEL"),
                        "ANTHROPIC_DEFAULT_SONNET_MODEL":os.getenv("ANTHROPIC_DEFAULT_SONNET_MODEL"),
                        "ANTHROPIC_DEFAULT_HAIKU_MODEL":os.getenv("ANTHROPIC_DEFAULT_HAIKU_MODEL"),
                        "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS":os.getenv("CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS")
                    },
                    extra_args={},
                ),
            ):
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if hasattr(block, "text"):
                            print(block.text)
                        elif hasattr(block, "name"):
                            print(f"Tool: {block.name}")
                elif isinstance(message, ResultMessage):
                    print(f"Done: {message.subtype}")
                    
            logging.info("Semiformalization phase completed successfully!")
        except Exception as e:
            logging.critical(f"CRITICAL (semiformalization phase): {e}")
            exit(1)
    else:
        logging.critical("CRITICAL (semiformalization phase): cannot have context without autofix enabled")
        exit(1)
        
    # Exploration phase
    
    if exploration:
        logging.info("Beginning exploration phase...")
        if not recurse and not context:
            try:
                # Load exploration phase system prompt and explorer, semiformalizer, and exploration-generator subagent prompts
                with open(_get_prompts_dir() / "EXPLORATION/FF.md", "r") as f:
                    EXPLORATION_PROMPT = f.read()
                with open(_get_subagents_dir() / "EXPLORATION/EXPLORER/F.md", "r") as f:
                    EXPLORER_SUBAGENT = f.read()
                with open(_get_subagents_dir() / "EXPLORATION/SEMIFORMALIZER/F.md", "r") as f:
                    SEMIFORMALIZER_SUBAGENT = f.read()
                with open(_get_subagents_dir() / "EXPLORATION/EXPLORATIONGENERATOR.md", "r") as f:
                    EXPLORATIONGENERATOR_SUBAGENT = f.read()
                
                async for message in query(
                    prompt=f"Explore `semiformal/` given specification language `language/` and source {source}.",
                    options=ClaudeAgentOptions(
                        tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebSearch", "WebFetch", "Agent", "Skill"],
                        allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebSearch", "WebFetch", "Agent", "Skill"],
                        agents={
                            "explorer": AgentDefinition(
                                description="Explorer subagent. Capable of searching the web and gathering sources for a specific assumption type.",
                                prompt=EXPLORER_SUBAGENT,
                                tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebSearch", "WebFetch", "Agent", "Skill"]
                            ),
                            "semiformalizer": AgentDefinition(
                                description="Semiformalizer subagent. Capable of semiformalizing gathered sources for specific assumption types into the existing semiformal translation.",
                                prompt=SEMIFORMALIZER_SUBAGENT,
                                tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebSearch", "WebFetch", "Agent", "Skill"]
                            ),
                            "exploration-generator": AgentDefinition(
                                description="ExplorationGenerator subagent. Capable of extending the IR specification language to accomodate new sources gathered during exploration.",
                                prompt=EXPLORATIONGENERATOR_SUBAGENT,
                                tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebSearch", "WebFetch", "Agent", "Skill"]
                            )
                        },
                        system_prompt=EXPLORATION_PROMPT,
                        mcp_servers=LEAN_MCP_SERVER,
                        permission_mode=PERMISSIONS,
                        continue_conversation=False,
                        max_budget_usd=exploration_budget,
                        disallowed_tools=[],
                        enable_file_checkpointing=True,
                        model="opus",
                        fallback_model="sonnet",
                        env={
                            "ANTHROPIC_BASE_URL":os.getenv("ANTHROPIC_BASE_URL"),
                            "ANTHROPIC_API_KEY":os.getenv("ANTHROPIC_API_KEY"),
                            "ANTHROPIC_AUTH_TOKEN":os.getenv("ANTHROPIC_AUTH_TOKEN"),
                            "ANTHROPIC_DEFAULT_OPUS_MODEL":os.getenv("ANTHROPIC_DEFAULT_OPUS_MODEL"),
                            "ANTHROPIC_DEFAULT_SONNET_MODEL":os.getenv("ANTHROPIC_DEFAULT_SONNET_MODEL"),
                            "ANTHROPIC_DEFAULT_HAIKU_MODEL":os.getenv("ANTHROPIC_DEFAULT_HAIKU_MODEL"),
                            "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS":os.getenv("CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS")
                        },
                        extra_args={},
                    ),
                ):
                    if isinstance(message, AssistantMessage):
                        for block in message.content:
                            if hasattr(block, "text"):
                                print(block.text)
                            elif hasattr(block, "name"):
                                print(f"Tool: {block.name}")
                    elif isinstance(message, ResultMessage):
                        print(f"Done: {message.subtype}")
                        
                logging.info("Exploration phase completed successfully!")
            except Exception as e:
                logging.critical(f"CRITICAL (exploration phase): {e}")
                exit(1)
        elif not recurse and context:
            try:
                # Load exploration phase system prompt and explorer, semiformalizer, and exploration-generator subagent prompts
                with open(_get_prompts_dir() / "EXPLORATION/FT.md", "r") as f:
                    EXPLORATION_PROMPT = f.read()
                with open(_get_subagents_dir() / "EXPLORATION/EXPLORER/T.md", "r") as f:
                    EXPLORER_SUBAGENT = f.read()
                with open(_get_subagents_dir() / "EXPLORATION/SEMIFORMALIZER/T.md", "r") as f:
                    SEMIFORMALIZER_SUBAGENT = f.read()
                with open(_get_subagents_dir() / "EXPLORATION/EXPLORATIONGENERATOR.md", "r") as f:
                    EXPLORATIONGENERATOR_SUBAGENT = f.read()
                
                async for message in query(
                    prompt=f"Explore `semiformal/` given specification language `language/` and source {source}. The Lean project is {project_path}.",
                    options=ClaudeAgentOptions(
                        tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebSearch", "WebFetch", "Agent", "Skill"],
                        allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebSearch", "WebFetch", "Agent", "Skill"],
                        agents={
                            "explorer": AgentDefinition(
                                description="Explorer subagent. Capable of searching the web and gathering sources for a specific assumption type.",
                                prompt=EXPLORER_SUBAGENT,
                                tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebSearch", "WebFetch", "Agent", "Skill"]
                            ),
                            "semiformalizer": AgentDefinition(
                                description="Semiformalizer subagent. Capable of semiformalizing gathered sources for specific assumption types into the existing semiformal translation.",
                                prompt=SEMIFORMALIZER_SUBAGENT,
                                tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebSearch", "WebFetch", "Agent", "Skill"]
                            ),
                            "exploration-generator": AgentDefinition(
                                description="ExplorationGenerator subagent. Capable of extending the IR specification language to accomodate new sources gathered during exploration.",
                                prompt=EXPLORATIONGENERATOR_SUBAGENT,
                                tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebSearch", "WebFetch", "Agent", "Skill"]
                            )
                        },
                        system_prompt=EXPLORATION_PROMPT,
                        mcp_servers=LEAN_MCP_SERVER,
                        permission_mode=PERMISSIONS,
                        continue_conversation=False,
                        max_budget_usd=exploration_budget,
                        disallowed_tools=[],
                        enable_file_checkpointing=True,
                        model="opus",
                        fallback_model="sonnet",
                        env={
                            "ANTHROPIC_BASE_URL":os.getenv("ANTHROPIC_BASE_URL"),
                            "ANTHROPIC_API_KEY":os.getenv("ANTHROPIC_API_KEY"),
                            "ANTHROPIC_AUTH_TOKEN":os.getenv("ANTHROPIC_AUTH_TOKEN"),
                            "ANTHROPIC_DEFAULT_OPUS_MODEL":os.getenv("ANTHROPIC_DEFAULT_OPUS_MODEL"),
                            "ANTHROPIC_DEFAULT_SONNET_MODEL":os.getenv("ANTHROPIC_DEFAULT_SONNET_MODEL"),
                            "ANTHROPIC_DEFAULT_HAIKU_MODEL":os.getenv("ANTHROPIC_DEFAULT_HAIKU_MODEL"),
                            "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS":os.getenv("CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS")
                        },
                        extra_args={},
                    ),
                ):
                    if isinstance(message, AssistantMessage):
                        for block in message.content:
                            if hasattr(block, "text"):
                                print(block.text)
                            elif hasattr(block, "name"):
                                print(f"Tool: {block.name}")
                    elif isinstance(message, ResultMessage):
                        print(f"Done: {message.subtype}")
                        
                logging.info("Exploration phase completed successfully!")
            except Exception as e:
                logging.critical(f"CRITICAL (exploration phase): {e}")
                exit(1)
        elif recurse and not context:
            try:
                # Load exploration phase system prompt and explorer, semiformalizer, and exploration-generator subagent prompts
                with open(_get_prompts_dir() / "EXPLORATION/TF.md", "r") as f:
                    EXPLORATION_PROMPT = f.read()
                with open(_get_subagents_dir() / "EXPLORATION/EXPLORER/F.md", "r") as f:
                    EXPLORER_SUBAGENT = f.read()
                with open(_get_subagents_dir() / "EXPLORATION/SEMIFORMALIZER/F.md", "r") as f:
                    SEMIFORMALIZER_SUBAGENT = f.read()
                with open(_get_subagents_dir() / "EXPLORATION/EXPLORATIONGENERATOR.md", "r") as f:
                    EXPLORATIONGENERATOR_SUBAGENT = f.read()
                
                async for message in query(
                    prompt=f"Explore `semiformal/` given specification language `language/` and source {source}.",
                    options=ClaudeAgentOptions(
                        tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebSearch", "WebFetch", "Agent", "Skill"],
                        allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebSearch", "WebFetch", "Agent", "Skill"],
                        agents={
                            "explorer": AgentDefinition(
                                description="Explorer subagent. Capable of searching the web and gathering sources for a specific assumption type.",
                                prompt=EXPLORER_SUBAGENT,
                                tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebSearch", "WebFetch", "Agent", "Skill"]
                            ),
                            "semiformalizer": AgentDefinition(
                                description="Semiformalizer subagent. Capable of semiformalizing gathered sources for specific assumption types into the existing semiformal translation.",
                                prompt=SEMIFORMALIZER_SUBAGENT,
                                tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebSearch", "WebFetch", "Agent", "Skill"]
                            ),
                            "exploration-generator": AgentDefinition(
                                description="ExplorationGenerator subagent. Capable of extending the IR specification language to accomodate new sources gathered during exploration.",
                                prompt=EXPLORATIONGENERATOR_SUBAGENT,
                                tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebSearch", "WebFetch", "Agent", "Skill"]
                            )
                        },
                        system_prompt=EXPLORATION_PROMPT,
                        mcp_servers=LEAN_MCP_SERVER,
                        permission_mode=PERMISSIONS,
                        continue_conversation=False,
                        max_budget_usd=exploration_budget,
                        disallowed_tools=[],
                        enable_file_checkpointing=True,
                        model="opus",
                        fallback_model="sonnet",
                        env={
                            "ANTHROPIC_BASE_URL":os.getenv("ANTHROPIC_BASE_URL"),
                            "ANTHROPIC_API_KEY":os.getenv("ANTHROPIC_API_KEY"),
                            "ANTHROPIC_AUTH_TOKEN":os.getenv("ANTHROPIC_AUTH_TOKEN"),
                            "ANTHROPIC_DEFAULT_OPUS_MODEL":os.getenv("ANTHROPIC_DEFAULT_OPUS_MODEL"),
                            "ANTHROPIC_DEFAULT_SONNET_MODEL":os.getenv("ANTHROPIC_DEFAULT_SONNET_MODEL"),
                            "ANTHROPIC_DEFAULT_HAIKU_MODEL":os.getenv("ANTHROPIC_DEFAULT_HAIKU_MODEL"),
                            "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS":os.getenv("CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS")
                        },
                        extra_args={},
                    ),
                ):
                    if isinstance(message, AssistantMessage):
                        for block in message.content:
                            if hasattr(block, "text"):
                                print(block.text)
                            elif hasattr(block, "name"):
                                print(f"Tool: {block.name}")
                    elif isinstance(message, ResultMessage):
                        print(f"Done: {message.subtype}")
                        
                logging.info("Exploration phase completed successfully!")
            except Exception as e:
                logging.critical(f"CRITICAL (exploration phase): {e}")
                exit(1)
        elif recurse and context:
            try:
                # Load exploration phase system prompt and explorer, semiformalizer, and exploration-generator subagent prompts
                with open(_get_prompts_dir() / "EXPLORATION/TT.md", "r") as f:
                    EXPLORATION_PROMPT = f.read()
                with open(_get_subagents_dir() / "EXPLORATION/EXPLORER/T.md", "r") as f:
                    EXPLORER_SUBAGENT = f.read()
                with open(_get_subagents_dir() / "EXPLORATION/SEMIFORMALIZER/T.md", "r") as f:
                    SEMIFORMALIZER_SUBAGENT = f.read()
                with open(_get_subagents_dir() / "EXPLORATION/EXPLORATIONGENERATOR.md", "r") as f:
                    EXPLORATIONGENERATOR_SUBAGENT = f.read()
                
                async for message in query(
                    prompt=f"Explore `semiformal/` given specification language `language/` and source {source}. The Lean project is {project_path}.",
                    options=ClaudeAgentOptions(
                        tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebSearch", "WebFetch", "Agent", "Skill"],
                        allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebSearch", "WebFetch", "Agent", "Skill"],
                        agents={
                            "explorer": AgentDefinition(
                                description="Explorer subagent. Capable of searching the web and gathering sources for a specific assumption type.",
                                prompt=EXPLORER_SUBAGENT,
                                tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebSearch", "WebFetch", "Agent", "Skill"]
                            ),
                            "semiformalizer": AgentDefinition(
                                description="Semiformalizer subagent. Capable of semiformalizing gathered sources for specific assumption types into the existing semiformal translation.",
                                prompt=SEMIFORMALIZER_SUBAGENT,
                                tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebSearch", "WebFetch", "Agent", "Skill"]
                            ),
                            "exploration-generator": AgentDefinition(
                                description="ExplorationGenerator subagent. Capable of extending the IR specification language to accomodate new sources gathered during exploration.",
                                prompt=EXPLORATIONGENERATOR_SUBAGENT,
                                tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebSearch", "WebFetch", "Agent", "Skill"]
                            )
                        },
                        system_prompt=EXPLORATION_PROMPT,
                        mcp_servers=LEAN_MCP_SERVER,
                        permission_mode=PERMISSIONS,
                        continue_conversation=False,
                        max_budget_usd=exploration_budget,
                        disallowed_tools=[],
                        enable_file_checkpointing=True,
                        model="opus",
                        fallback_model="sonnet",
                        env={
                            "ANTHROPIC_BASE_URL":os.getenv("ANTHROPIC_BASE_URL"),
                            "ANTHROPIC_API_KEY":os.getenv("ANTHROPIC_API_KEY"),
                            "ANTHROPIC_AUTH_TOKEN":os.getenv("ANTHROPIC_AUTH_TOKEN"),
                            "ANTHROPIC_DEFAULT_OPUS_MODEL":os.getenv("ANTHROPIC_DEFAULT_OPUS_MODEL"),
                            "ANTHROPIC_DEFAULT_SONNET_MODEL":os.getenv("ANTHROPIC_DEFAULT_SONNET_MODEL"),
                            "ANTHROPIC_DEFAULT_HAIKU_MODEL":os.getenv("ANTHROPIC_DEFAULT_HAIKU_MODEL"),
                            "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS":os.getenv("CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS")
                        },
                        extra_args={},
                    ),
                ):
                    if isinstance(message, AssistantMessage):
                        for block in message.content:
                            if hasattr(block, "text"):
                                print(block.text)
                            elif hasattr(block, "name"):
                                print(f"Tool: {block.name}")
                    elif isinstance(message, ResultMessage):
                        print(f"Done: {message.subtype}")
                        
                logging.info("Exploration phase completed successfully!")
            except Exception as e:
                logging.critical(f"CRITICAL (exploration phase): {e}")
                exit(1)
        else:
            logging.critical("CRITICAL (exploration phase): reached unreachable code")
            exit(1)
    else:
        logging.info("Exploration phase skipped.")
        
    # Preparation phase
    
    logging.info("Beginning preparation phase...")
    
    if not context:
        try:
            # Load preparation phase system prompt
            with open(_get_prompts_dir() / "PREPARATION/F.md", "r") as f:
                PREPARATION_PROMPT = f.read()
            
            async for message in query(
                prompt=f"Prepare to formalize {source}.",
                options=ClaudeAgentOptions(
                    tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebSearch", "WebFetch", "Agent", "Skill"],
                    allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebSearch", "WebFetch", "Agent", "Skill"],
                    agents={},
                    system_prompt=PREPARATION_PROMPT,
                    mcp_servers=LEAN_MCP_SERVER,
                    permission_mode=PERMISSIONS,
                    continue_conversation=False,
                    max_budget_usd=preparation_budget,
                    disallowed_tools=[],
                    enable_file_checkpointing=True,
                    model="opus",
                    fallback_model="sonnet",
                    env={
                        "ANTHROPIC_BASE_URL":os.getenv("ANTHROPIC_BASE_URL"),
                        "ANTHROPIC_API_KEY":os.getenv("ANTHROPIC_API_KEY"),
                        "ANTHROPIC_AUTH_TOKEN":os.getenv("ANTHROPIC_AUTH_TOKEN"),
                        "ANTHROPIC_DEFAULT_OPUS_MODEL":os.getenv("ANTHROPIC_DEFAULT_OPUS_MODEL"),
                        "ANTHROPIC_DEFAULT_SONNET_MODEL":os.getenv("ANTHROPIC_DEFAULT_SONNET_MODEL"),
                        "ANTHROPIC_DEFAULT_HAIKU_MODEL":os.getenv("ANTHROPIC_DEFAULT_HAIKU_MODEL"),
                        "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS":os.getenv("CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS")
                    },
                    extra_args={},
                ),
            ):
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if hasattr(block, "text"):
                            print(block.text)
                        elif hasattr(block, "name"):
                            print(f"Tool: {block.name}")
                elif isinstance(message, ResultMessage):
                    print(f"Done: {message.subtype}")
                    
            logging.info("Preparation phase completed successfully!")
        except Exception as e:
            logging.critical(f"CRITICAL (preparation phase): {e}")
            exit(1)
    elif context:
        try:
            # Load preparation phase system prompt
            with open(_get_prompts_dir() / "PREPARATION/T.md", "r") as f:
                PREPARATION_PROMPT = f.read()
            
            async for message in query(
                prompt=f"Prepare to formalize {source}. The Lean project is {project_path}.",
                options=ClaudeAgentOptions(
                    tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebSearch", "WebFetch", "Agent", "Skill"],
                    allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebSearch", "WebFetch", "Agent", "Skill"],
                    agents={},
                    system_prompt=PREPARATION_PROMPT,
                    mcp_servers=LEAN_MCP_SERVER,
                    permission_mode=PERMISSIONS,
                    continue_conversation=False,
                    max_budget_usd=preparation_budget,
                    disallowed_tools=[],
                    enable_file_checkpointing=True,
                    model="opus",
                    fallback_model="sonnet",
                    env={
                        "ANTHROPIC_BASE_URL":os.getenv("ANTHROPIC_BASE_URL"),
                        "ANTHROPIC_API_KEY":os.getenv("ANTHROPIC_API_KEY"),
                        "ANTHROPIC_AUTH_TOKEN":os.getenv("ANTHROPIC_AUTH_TOKEN"),
                        "ANTHROPIC_DEFAULT_OPUS_MODEL":os.getenv("ANTHROPIC_DEFAULT_OPUS_MODEL"),
                        "ANTHROPIC_DEFAULT_SONNET_MODEL":os.getenv("ANTHROPIC_DEFAULT_SONNET_MODEL"),
                        "ANTHROPIC_DEFAULT_HAIKU_MODEL":os.getenv("ANTHROPIC_DEFAULT_HAIKU_MODEL"),
                        "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS":os.getenv("CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS")
                    },
                    extra_args={},
                ),
            ):
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if hasattr(block, "text"):
                            print(block.text)
                        elif hasattr(block, "name"):
                            print(f"Tool: {block.name}")
                elif isinstance(message, ResultMessage):
                    print(f"Done: {message.subtype}")
                    
            logging.info("Preparation phase completed successfully!")
        except Exception as e:
            logging.critical(f"CRITICAL (preparation phase): {e}")
            exit(1)
    else:
        logging.critical("CRITICAL (preparation phase): reached unreachable code")
        exit(1)
        
    # Formalization phase
    
    logging.info("Beginning formalization phase...")
    
    if not context:
        try:
            # Load formalization phase system prompt and declaration-formalizer and proof-formalizer subagent prompts
            with open(_get_prompts_dir() / "FORMALIZATION/F.md", "r") as f:
                FORMALIZATION_PROMPT = f.read()
            with open(_get_subagents_dir() / "FORMALIZATION/DECLARATIONFORMALIZER/F.md", "r") as f:
                DECLARATIONFORMALIZER_SUBAGENT = f.read()
            with open(_get_subagents_dir() / "FORMALIZATION/PROOFFORMALIZER/F.md", "r") as f:
                PROOFFORMALIZER_SUBAGENT = f.read()
            
            async for message in query(
                prompt=f"Formalize {source} into {project_path}.",
                options=ClaudeAgentOptions(
                    tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebSearch", "WebFetch", "Agent", "Skill"],
                    allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebSearch", "WebFetch", "Agent", "Skill"],
                    agents={
                        "declaration-formalizer": AgentDefinition(
                            description="DeclarationFormalizer subagent. Capable of formalizing a declaration or statement of a specific chunk into Lean4.",
                            prompt=DECLARATIONFORMALIZER_SUBAGENT,
                            tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebSearch", "WebFetch", "Agent", "Skill"]
                        ),
                        "proof-formalizer": AgentDefinition(
                            description="ProofFormalizer subagent. Capable of formalizing the proof of a specific chunk into Lean4.",
                            prompt=PROOFFORMALIZER_SUBAGENT,
                            tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebSearch", "WebFetch", "Agent", "Skill"]
                        )
                    },
                    system_prompt=FORMALIZATION_PROMPT,
                    mcp_servers=LEAN_MCP_SERVER,
                    permission_mode=PERMISSIONS,
                    continue_conversation=False,
                    max_budget_usd=formalization_budget,
                    disallowed_tools=[],
                    enable_file_checkpointing=True,
                    model="opus",
                    fallback_model="sonnet",
                    env={
                        "ANTHROPIC_BASE_URL":os.getenv("ANTHROPIC_BASE_URL"),
                        "ANTHROPIC_API_KEY":os.getenv("ANTHROPIC_API_KEY"),
                        "ANTHROPIC_AUTH_TOKEN":os.getenv("ANTHROPIC_AUTH_TOKEN"),
                        "ANTHROPIC_DEFAULT_OPUS_MODEL":os.getenv("ANTHROPIC_DEFAULT_OPUS_MODEL"),
                        "ANTHROPIC_DEFAULT_SONNET_MODEL":os.getenv("ANTHROPIC_DEFAULT_SONNET_MODEL"),
                        "ANTHROPIC_DEFAULT_HAIKU_MODEL":os.getenv("ANTHROPIC_DEFAULT_HAIKU_MODEL"),
                        "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS":os.getenv("CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS")
                    },
                    extra_args={},
                ),
            ):
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if hasattr(block, "text"):
                            print(block.text)
                        elif hasattr(block, "name"):
                            print(f"Tool: {block.name}")
                elif isinstance(message, ResultMessage):
                    print(f"Done: {message.subtype}")
                    
            logging.info("Formalization phase completed successfully!")
        except Exception as e:
            logging.critical(f"CRITICAL (formalization phase): {e}")
            exit(1)
    elif context:
        try:
            # Load formalization phase system prompt and declaration-formalizer and proof-formalizer subagent prompts
            with open(_get_prompts_dir() / "FORMALIZATION/T.md", "r") as f:
                FORMALIZATION_PROMPT = f.read()
            with open(_get_subagents_dir() / "FORMALIZATION/DECLARATIONFORMALIZER/T.md", "r") as f:
                DECLARATIONFORMALIZER_SUBAGENT = f.read()
            with open(_get_subagents_dir() / "FORMALIZATION/PROOFFORMALIZER/T.md", "r") as f:
                PROOFFORMALIZER_SUBAGENT = f.read()
            
            async for message in query(
                prompt=f"Formalize {source} into {project_path}.",
                options=ClaudeAgentOptions(
                    tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebSearch", "WebFetch", "Agent", "Skill"],
                    allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebSearch", "WebFetch", "Agent", "Skill"],
                    agents={
                        "declaration-formalizer": AgentDefinition(
                            description="DeclarationFormalizer subagent. Capable of formalizing a declaration or statement of a specific chunk into Lean4.",
                            prompt=DECLARATIONFORMALIZER_SUBAGENT,
                            tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebSearch", "WebFetch", "Agent", "Skill"]
                        ),
                        "proof-formalizer": AgentDefinition(
                            description="ProofFormalizer subagent. Capable of formalizing the proof of a specific chunk into Lean4.",
                            prompt=PROOFFORMALIZER_SUBAGENT,
                            tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebSearch", "WebFetch", "Agent", "Skill"]
                        )
                    },
                    system_prompt=FORMALIZATION_PROMPT,
                    mcp_servers=LEAN_MCP_SERVER,
                    permission_mode=PERMISSIONS,
                    continue_conversation=False,
                    max_budget_usd=formalization_budget,
                    disallowed_tools=[],
                    enable_file_checkpointing=True,
                    model="opus",
                    fallback_model="sonnet",
                    env={
                        "ANTHROPIC_BASE_URL":os.getenv("ANTHROPIC_BASE_URL"),
                        "ANTHROPIC_API_KEY":os.getenv("ANTHROPIC_API_KEY"),
                        "ANTHROPIC_AUTH_TOKEN":os.getenv("ANTHROPIC_AUTH_TOKEN"),
                        "ANTHROPIC_DEFAULT_OPUS_MODEL":os.getenv("ANTHROPIC_DEFAULT_OPUS_MODEL"),
                        "ANTHROPIC_DEFAULT_SONNET_MODEL":os.getenv("ANTHROPIC_DEFAULT_SONNET_MODEL"),
                        "ANTHROPIC_DEFAULT_HAIKU_MODEL":os.getenv("ANTHROPIC_DEFAULT_HAIKU_MODEL"),
                        "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS":os.getenv("CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS")
                    },
                    extra_args={},
                ),
            ):
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if hasattr(block, "text"):
                            print(block.text)
                        elif hasattr(block, "name"):
                            print(f"Tool: {block.name}")
                elif isinstance(message, ResultMessage):
                    print(f"Done: {message.subtype}")
                    
            logging.info("Formalization phase completed successfully!")
        except Exception as e:
            logging.critical(f"CRITICAL (formalization phase): {e}")
            exit(1)
    else:
        logging.critical("CRITICAL (formalization phase): reached unreachable code")
        exit(1)
        
    # Critic phase
    
    logging.info("Beginning critic phase...")

    if not context:
        try:
            # Load critic phase system prompt and declaration-formalizer and proof-formalizer subagent prompts
            with open(_get_prompts_dir() / "CRITIC.md", "r") as f:
                CRITIC_PROMPT = f.read()
            with open(_get_subagents_dir() / "CRITIC/DECLARATIONFORMALIZER/F.md", "r") as f:
                DECLARATIONFORMALIZER_SUBAGENT = f.read()
            with open(_get_subagents_dir() / "CRITIC/PROOFFORMALIZER/F.md", "r") as f:
                PROOFFORMALIZER_SUBAGENT = f.read()
            
            async for message in query(
                prompt=f"Critique {project_path} given source {source}, semiformalization `semiformal/`, and specification language `language/`.",
                options=ClaudeAgentOptions(
                    tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebSearch", "WebFetch", "Agent", "Skill"],
                    allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebSearch", "WebFetch", "Agent", "Skill"],
                    agents={
                        "declaration-formalizer": AgentDefinition(
                            description="DeclarationFormalizer subagent. Capable of formalizing a declaration or statement of a specific chunk into Lean4.",
                            prompt=DECLARATIONFORMALIZER_SUBAGENT,
                            tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebSearch", "WebFetch", "Agent", "Skill"]
                        ),
                        "proof-formalizer": AgentDefinition(
                            description="ProofFormalizer subagent. Capable of formalizing the proof of a specific chunk into Lean4.",
                            prompt=PROOFFORMALIZER_SUBAGENT,
                            tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebSearch", "WebFetch", "Agent", "Skill"]
                        )
                    },
                    system_prompt=CRITIC_PROMPT,
                    mcp_servers=LEAN_MCP_SERVER,
                    permission_mode=PERMISSIONS,
                    continue_conversation=False,
                    max_budget_usd=critic_budget,
                    disallowed_tools=[],
                    enable_file_checkpointing=True,
                    model="opus",
                    fallback_model="sonnet",
                    env={
                        "ANTHROPIC_BASE_URL":os.getenv("ANTHROPIC_BASE_URL"),
                        "ANTHROPIC_API_KEY":os.getenv("ANTHROPIC_API_KEY"),
                        "ANTHROPIC_AUTH_TOKEN":os.getenv("ANTHROPIC_AUTH_TOKEN"),
                        "ANTHROPIC_DEFAULT_OPUS_MODEL":os.getenv("ANTHROPIC_DEFAULT_OPUS_MODEL"),
                        "ANTHROPIC_DEFAULT_SONNET_MODEL":os.getenv("ANTHROPIC_DEFAULT_SONNET_MODEL"),
                        "ANTHROPIC_DEFAULT_HAIKU_MODEL":os.getenv("ANTHROPIC_DEFAULT_HAIKU_MODEL"),
                        "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS":os.getenv("CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS")
                    },
                    extra_args={},
                ),
            ):
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if hasattr(block, "text"):
                            print(block.text)
                        elif hasattr(block, "name"):
                            print(f"Tool: {block.name}")
                elif isinstance(message, ResultMessage):
                    print(f"Done: {message.subtype}")
                    
            logging.info("Critic phase completed successfully!")
        except Exception as e:
            logging.critical(f"CRITICAL (critic phase): {e}")
            exit(1)
    elif context:
        try:
            # Load critic phase system prompt and declaration-formalizer and proof-formalizer subagent prompts
            with open(_get_prompts_dir() / "CRITIC.md", "r") as f:
                CRITIC_PROMPT = f.read()
            with open(_get_subagents_dir() / "CRITIC/DECLARATIONFORMALIZER/T.md", "r") as f:
                DECLARATIONFORMALIZER_SUBAGENT = f.read()
            with open(_get_subagents_dir() / "CRITIC/PROOFFORMALIZER/T.md", "r") as f:
                PROOFFORMALIZER_SUBAGENT = f.read()
            
            async for message in query(
                prompt=f"Critique {project_path} given source {source}, semiformalization `semiformal/`, and specification language `language/`.",
                options=ClaudeAgentOptions(
                    tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebSearch", "WebFetch", "Agent", "Skill"],
                    allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebSearch", "WebFetch", "Agent", "Skill"],
                    agents={
                        "declaration-formalizer": AgentDefinition(
                            description="DeclarationFormalizer subagent. Capable of formalizing a declaration or statement of a specific chunk into Lean4.",
                            prompt=DECLARATIONFORMALIZER_SUBAGENT,
                            tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebSearch", "WebFetch", "Agent", "Skill"]
                        ),
                        "proof-formalizer": AgentDefinition(
                            description="ProofFormalizer subagent. Capable of formalizing the proof of a specific chunk into Lean4.",
                            prompt=PROOFFORMALIZER_SUBAGENT,
                            tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebSearch", "WebFetch", "Agent", "Skill"]
                        )
                    },
                    system_prompt=CRITIC_PROMPT,
                    mcp_servers=LEAN_MCP_SERVER,
                    permission_mode=PERMISSIONS,
                    continue_conversation=False,
                    max_budget_usd=critic_budget,
                    disallowed_tools=[],
                    enable_file_checkpointing=True,
                    model="opus",
                    fallback_model="sonnet",
                    env={
                        "ANTHROPIC_BASE_URL":os.getenv("ANTHROPIC_BASE_URL"),
                        "ANTHROPIC_API_KEY":os.getenv("ANTHROPIC_API_KEY"),
                        "ANTHROPIC_AUTH_TOKEN":os.getenv("ANTHROPIC_AUTH_TOKEN"),
                        "ANTHROPIC_DEFAULT_OPUS_MODEL":os.getenv("ANTHROPIC_DEFAULT_OPUS_MODEL"),
                        "ANTHROPIC_DEFAULT_SONNET_MODEL":os.getenv("ANTHROPIC_DEFAULT_SONNET_MODEL"),
                        "ANTHROPIC_DEFAULT_HAIKU_MODEL":os.getenv("ANTHROPIC_DEFAULT_HAIKU_MODEL"),
                        "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS":os.getenv("CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS")
                    },
                    extra_args={},
                ),
            ):
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if hasattr(block, "text"):
                            print(block.text)
                        elif hasattr(block, "name"):
                            print(f"Tool: {block.name}")
                elif isinstance(message, ResultMessage):
                    print(f"Done: {message.subtype}")
                    
            logging.info("Critic phase completed successfully!")
        except Exception as e:
            logging.critical(f"CRITICAL (critic phase): {e}")
            exit(1)
    else:
        logging.critical("CRITICAL (critic phase): reached unreachable code")
        exit(1)
        
    # Cleanup
    
    logging.info("Cleaning up...")
    
    try:    
        if not save_spec:
            spec_dir = Path("language")
            subprocess.run(["rm", "-rf", str(spec_dir)], check=True)
        
        if not save_semiformalization:
            semiformal_dir = Path("semiformal")
            subprocess.run(["rm", "-rf", str(semiformal_dir)], check=True)
    except Exception as e:
        logging.error(f"ERROR (clean up): {e}")
        
    logging.info("Clean up completed successfully!")
        
    logging.info("Unity has completed!")
    return 0
