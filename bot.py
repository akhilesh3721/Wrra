"""
WRRA SMP Discord Bot
Manages a Pterodactyl-hosted Minecraft server via text + slash commands
and a button-based control panel.
"""

import os
import sys
import logging
import asyncio
from datetime import datetime, timezone

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks
from mcstatus import JavaServer

# ─────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────

sys.stdout.reconfigure(line_buffering=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("wrra-bot")

TOKEN = os.getenv("TOKEN")
PTERO_API = os.getenv("PTERO_API")

if not TOKEN or not PTERO_API:
    log.error("TOKEN and PTERO_API environment variables are required.")
    sys.exit(1)

PANEL_URL = os.getenv("PANEL_URL", "https://mcservers.in")
SERVER_ID = os.getenv("SERVER_ID", "51f45a64")

# Address players actually connect to / that we poll for live status.
# Update this one line if the address ever changes (e.g. after moving to playit.gg).
MC_STATUS_ADDR = os.getenv("MC_STATUS_ADDR","Inv-2.aryncloud.in:19149")

STATUS_CHANNEL_ID = int(os.getenv("STATUS_CHANNEL_ID","1539325550533939341"))

_default_owners = "1365256422585274398,1313747040362168393,1168049016559378475"
_ENV_OWNER_IDS = {int(x) for x in os.getenv("OWNER_IDS", _default_owners).split(",") if x}
OWNERS_FILE = os.getenv("OWNERS_FILE", "owners.json")

HEADERS = {
    "Authorization": f"Bearer {PTERO_API}",
    "Accept": "Application/vnd.pterodactyl.v1+json",
    "Content-Type": "application/json",
}

BRAND_NAME = "WRRA SMP"
BRAND_TAGLINE = "⚔️ Survival • Economy • Adventure ⚔️"
COLOR_ONLINE = 0x57F287
COLOR_OFFLINE = 0xED4245
COLOR_INFO = 0x5865F2
COLOR_WARN = 0xFEE75C

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
status_message: discord.Message | None = None


# ─────────────────────────────────────────────────────────────
# Owner management (env-defined owners + runtime-added owners,
# persisted to disk so they survive restarts)
# ─────────────────────────────────────────────────────────────

import json

def load_extra_owners() -> set[int]:
    try:
        with open(OWNERS_FILE, "r") as f:
            return {int(x) for x in json.load(f)}
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        return set()


def save_extra_owners(extra: set[int]):
    with open(OWNERS_FILE, "w") as f:
        json.dump(sorted(extra), f)


EXTRA_OWNER_IDS: set[int] = load_extra_owners()


def all_owner_ids() -> set[int]:
    return _ENV_OWNER_IDS | EXTRA_OWNER_IDS


def is_owner_id(user_id: int) -> bool:
    return user_id in all_owner_ids()


def add_owner(user_id: int) -> bool:
    if user_id in all_owner_ids():
        return False
    EXTRA_OWNER_IDS.add(user_id)
    save_extra_owners(EXTRA_OWNER_IDS)
    return True


def remove_owner(user_id: int) -> tuple[bool, str]:
    if user_id in _ENV_OWNER_IDS:
        return False, "This owner is set via environment config and can't be removed with a command."
    if user_id not in EXTRA_OWNER_IDS:
        return False, "That user isn't an owner."
    if len(all_owner_ids()) <= 1:
        return False, "Can't remove the last remaining owner."
    EXTRA_OWNER_IDS.discard(user_id)
    save_extra_owners(EXTRA_OWNER_IDS)
    return True, ""


def footer(embed: discord.Embed) -> discord.Embed:
    embed.set_footer(text=BRAND_NAME, icon_url=None)
    embed.timestamp = datetime.now(timezone.utc)
    return embed


# ─────────────────────────────────────────────────────────────
# HTTP session (single reusable aiohttp session, non-blocking)
# ─────────────────────────────────────────────────────────────

class Ptero:
    """Thin async wrapper around the Pterodactyl client API."""

    def __init__(self):
        self.session: aiohttp.ClientSession | None = None

    async def start(self):
        self.session = aiohttp.ClientSession(
            headers=HEADERS, timeout=aiohttp.ClientTimeout(total=10)
        )

    async def close(self):
        if self.session:
            await self.session.close()

    async def _request(self, method: str, path: str, **kwargs):
        url = f"{PANEL_URL}{path}"
        try:
            async with self.session.request(method, url, **kwargs) as r:
                text = await r.text()
                data = None
                if r.content_type == "application/json" and text:
                    try:
                        data = await r.json()
                    except aiohttp.ContentTypeError:
                        data = None
                log.info("%s %s -> %s", method, path, r.status)
                return r.status, data, text
        except asyncio.TimeoutError:
            log.warning("Ptero request timed out: %s %s", method, path)
            return 0, None, "timeout"
        except aiohttp.ClientError as e:
            log.warning("Ptero request failed: %s %s (%s)", method, path, e)
            return 0, None, str(e)

    async def resources(self):
        return await self._request("GET", f"/api/client/servers/{SERVER_ID}/resources")

    async def power(self, signal: str):
        return await self._request(
            "POST", f"/api/client/servers/{SERVER_ID}/power", json={"signal": signal}
        )

    async def command(self, command: str):
        return await self._request(
            "POST", f"/api/client/servers/{SERVER_ID}/command", json={"command": command}
        )


ptero = Ptero()


async def get_mc_status():
    """Returns a mcstatus status object, or None if unreachable."""
    try:
        server = JavaServer.lookup(MC_STATUS_ADDR)
        return await asyncio.to_thread(server.status)
    except Exception as e:
        log.info("MC status lookup failed: %s", e)
        return None


# ─────────────────────────────────────────────────────────────
# Embed builders
# ─────────────────────────────────────────────────────────────

def build_status_embed(status) -> discord.Embed:
    if status is None:
        embed = discord.Embed(
            title=f"🔴 {BRAND_NAME}",
            description="Server Offline",
            color=COLOR_OFFLINE,
        )
        return footer(embed)

    embed = discord.Embed(
        title=f"🟢 {BRAND_NAME}",
        description=BRAND_TAGLINE,
        color=COLOR_ONLINE,
    )
    embed.add_field(name="Status", value="🟢 Online", inline=True)
    embed.add_field(
        name="Players", value=f"{status.players.online}/{status.players.max}", inline=True
    )
    embed.add_field(name="Latency", value=f"{round(status.latency)}ms", inline=True)
    embed.add_field(name="Version", value=status.version.name, inline=True)
    return footer(embed)


def build_resource_embed(state: str, ram_mb: float, cpu: float) -> discord.Embed:
    color = COLOR_ONLINE if state == "running" else COLOR_WARN
    embed = discord.Embed(title="📊 Server Resources", color=color)
    embed.add_field(name="State", value=state.title(), inline=True)
    embed.add_field(name="RAM", value=f"{ram_mb} MB", inline=True)
    embed.add_field(name="CPU", value=f"{cpu}%", inline=True)
    return footer(embed)


def build_error_embed(title: str, detail: str) -> discord.Embed:
    embed = discord.Embed(title=f"❌ {title}", description=detail, color=COLOR_OFFLINE)
    return footer(embed)


def build_denied_embed() -> discord.Embed:
    return discord.Embed(
        title="🚫 Not Allowed",
        description="Only server owners can use this.",
        color=COLOR_OFFLINE,
    )


# ─────────────────────────────────────────────────────────────
# Shared action helpers (used by both text + slash commands)
# ─────────────────────────────────────────────────────────────

async def do_power_action(signal: str) -> tuple[bool, str]:
    status, _, text = await ptero.power(signal)
    if status in (200, 204):
        return True, ""
    if status == 0:
        return False, "Could not reach the panel (timeout or connection error)."
    return False, f"Panel returned HTTP {status}."


async def do_console_command(command: str) -> tuple[bool, str]:
    status, _, text = await ptero.command(command)
    if status in (200, 204):
        return True, ""
    if status == 0:
        return False, "Could not reach the panel (timeout or connection error)."
    return False, f"Panel returned HTTP {status}."


async def fetch_resources():
    status, data, text = await ptero.resources()
    if status != 200 or not data:
        return None, status
    attrs = data["attributes"]
    state = attrs["current_state"]
    ram = round(attrs["resources"]["memory_bytes"] / 1024 / 1024, 2)
    cpu = round(attrs["resources"]["cpu_absolute"], 2)
    return (state, ram, cpu), status


# ─────────────────────────────────────────────────────────────
# Control panel UI (buttons)
# ─────────────────────────────────────────────────────────────

class ConfirmView(discord.ui.View):
    """Yes/No confirmation for destructive actions."""

    def __init__(self, owner_id: int, action_label: str):
        super().__init__(timeout=30)
        self.owner_id = owner_id
        self.action_label = action_label
        self.confirmed: bool | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "This confirmation isn't for you.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger, emoji="⚠️")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.confirmed = True
        self.stop()
        await interaction.response.edit_message(
            content=f"✅ {self.action_label} confirmed.", view=None
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.confirmed = False
        self.stop()
        await interaction.response.edit_message(content="Cancelled.", view=None)


class CommandModal(discord.ui.Modal, title="Run Console Command"):
    command_input = discord.ui.TextInput(
        label="Command", placeholder="e.g. say Hello everyone!", max_length=200
    )

    async def on_submit(self, interaction: discord.Interaction):
        command = self.command_input.value
        await interaction.response.defer(ephemeral=True)
        ok, err = await do_console_command(command)
        if ok:
            await interaction.followup.send(f"✅ Executed: `{command}`", ephemeral=True)
        else:
            await interaction.followup.send(f"❌ {err}", ephemeral=True)


class ControlPanelView(discord.ui.View):
    """Persistent owner-only control panel with Start/Stop/Restart/Command/Refresh."""

    def __init__(self):
        super().__init__(timeout=None)

    async def _guarded(self, interaction: discord.Interaction) -> bool:
        if not is_owner_id(interaction.user.id):
            await interaction.response.send_message(embed=build_denied_embed(), ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Start", style=discord.ButtonStyle.success, emoji="🟢", custom_id="panel_start")
    async def start_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._guarded(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        ok, err = await do_power_action("start")
        msg = "🟢 Server starting!" if ok else f"❌ {err}"
        await interaction.followup.send(msg, ephemeral=True)

    @discord.ui.button(label="Stop", style=discord.ButtonStyle.danger, emoji="🔴", custom_id="panel_stop")
    async def stop_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._guarded(interaction):
            return
        view = ConfirmView(interaction.user.id, "Stop")
        await interaction.response.send_message(
            "Stop the server? Players will be disconnected.", view=view, ephemeral=True
        )
        await view.wait()
        if view.confirmed:
            ok, err = await do_power_action("stop")
            await interaction.followup.send(
                "🔴 Server stopping!" if ok else f"❌ {err}", ephemeral=True
            )

    @discord.ui.button(label="Restart", style=discord.ButtonStyle.primary, emoji="🔄", custom_id="panel_restart")
    async def restart_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._guarded(interaction):
            return
        view = ConfirmView(interaction.user.id, "Restart")
        await interaction.response.send_message(
            "Restart the server? Players will be disconnected briefly.", view=view, ephemeral=True
        )
        await view.wait()
        if view.confirmed:
            ok, err = await do_power_action("restart")
            await interaction.followup.send(
                "🔄 Server restarting!" if ok else f"❌ {err}", ephemeral=True
            )

    @discord.ui.button(label="Console", style=discord.ButtonStyle.secondary, emoji="🖥️", custom_id="panel_cmd")
    async def cmd_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._guarded(interaction):
            return
        await interaction.response.send_modal(CommandModal())

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, emoji="🔃", custom_id="panel_refresh")
    async def refresh_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        result, http_status = await fetch_resources()
        if result is None:
            await interaction.followup.send(
                f"❌ Panel returned HTTP {http_status}", ephemeral=True
            )
            return
        state, ram, cpu = result
        await interaction.followup.send(embed=build_resource_embed(state, ram, cpu), ephemeral=True)


# ─────────────────────────────────────────────────────────────
# Background status-channel embed
# ─────────────────────────────────────────────────────────────

@tasks.loop(minutes=1)
async def update_status():
    global status_message

    channel = bot.get_channel(STATUS_CHANNEL_ID)
    if channel is None:
        log.warning("Status channel %s not found (bad ID or missing access).", STATUS_CHANNEL_ID)
        return

    mc_status = await get_mc_status()
    embed = build_status_embed(mc_status)

    try:
        if status_message is None:
            status_message = await channel.send(embed=embed)
        else:
            await status_message.edit(embed=embed)
    except discord.NotFound:
        # Message was deleted — recreate it.
        status_message = await channel.send(embed=embed)
    except discord.HTTPException as e:
        log.warning("Failed to update status message: %s", e)


@update_status.error
async def update_status_error(error):
    log.error("Status loop crashed: %s", error)


# ─────────────────────────────────────────────────────────────
# Events
# ─────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    await ptero.start()
    bot.add_view(ControlPanelView())  # re-register for persistence across restarts
    log.info("Logged in as %s", bot.user)

    await bot.tree.sync()
    log.info("Slash commands synced")

    if not update_status.is_running():
        update_status.start()


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    await bot.process_commands(message)


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    log.error("Command error in !%s: %s", ctx.command, error)
    await ctx.send(embed=build_error_embed("Something went wrong", str(error)))


# ─────────────────────────────────────────────────────────────
# Text commands
# ─────────────────────────────────────────────────────────────

@bot.command()
async def ping(ctx):
    await ctx.send(f"🏓 Pong! {round(bot.latency * 1000)}ms")


@bot.command(name="help")
async def help_cmd(ctx):
    embed = discord.Embed(
        title=f"🤖 {BRAND_NAME} Bot", description="Minecraft Server Management", color=COLOR_INFO
    )
    embed.add_field(name="📊 Information", value="`!ping` `!status` `!serverstatus`", inline=False)
    embed.add_field(name="⚙️ Server Controls", value="`!start` `!stop` `!restart`", inline=False)
    embed.add_field(name="🖥️ Console", value="`!cmd <command>`", inline=False)
    embed.add_field(name="🎛️ Control Panel", value="`!panel` — button-based controls", inline=False)
    embed.add_field(
        name="👑 Owners",
        value="`!addowner @user` `!removeowner @user` `!listowners`",
        inline=False,
    )
    await ctx.send(embed=footer(embed))


@bot.command()
async def serverstatus(ctx):
    mc_status = await get_mc_status()
    await ctx.send(embed=build_status_embed(mc_status))


@bot.command()
async def status(ctx):
    result, http_status = await fetch_resources()
    if result is None:
        return await ctx.send(embed=build_error_embed("Status Error", f"HTTP {http_status}"))
    state, ram, cpu = result
    await ctx.send(embed=build_resource_embed(state, ram, cpu))


@bot.command()
async def panel(ctx):
    embed = discord.Embed(
        title=f"🎛️ {BRAND_NAME} Control Panel",
        description="Owner-only controls. Buttons are visible to everyone but only work for owners.",
        color=COLOR_INFO,
    )
    await ctx.send(embed=footer(embed), view=ControlPanelView())


@bot.command()
async def start(ctx):
    if not is_owner_id(ctx.author.id):
        return await ctx.send(embed=build_denied_embed())
    ok, err = await do_power_action("start")
    await ctx.send("🟢 Server starting!" if ok else f"❌ {err}")


@bot.command()
async def stop(ctx):
    if not is_owner_id(ctx.author.id):
        return await ctx.send(embed=build_denied_embed())
    ok, err = await do_power_action("stop")
    await ctx.send("🔴 Server stopping!" if ok else f"❌ {err}")


@bot.command()
async def restart(ctx):
    if not is_owner_id(ctx.author.id):
        return await ctx.send(embed=build_denied_embed())
    ok, err = await do_power_action("restart")
    await ctx.send("🔄 Server restarting!" if ok else f"❌ {err}")


@bot.command(name="cmd")
async def cmd_text(ctx, *, command: str):
    if not is_owner_id(ctx.author.id):
        return await ctx.send(embed=build_denied_embed())
    ok, err = await do_console_command(command)
    await ctx.send(f"✅ Executed: `{command}`" if ok else f"❌ {err}")


@bot.command(name="addowner")
async def addowner_text(ctx, member: discord.Member):
    if not is_owner_id(ctx.author.id):
        return await ctx.send(embed=build_denied_embed())
    if add_owner(member.id):
        await ctx.send(f"✅ {member.mention} is now an owner.")
    else:
        await ctx.send(f"⚠️ {member.mention} is already an owner.")


@bot.command(name="removeowner")
async def removeowner_text(ctx, member: discord.Member):
    if not is_owner_id(ctx.author.id):
        return await ctx.send(embed=build_denied_embed())
    ok, err = remove_owner(member.id)
    await ctx.send(f"✅ {member.mention} removed as owner." if ok else f"❌ {err}")


@bot.command(name="listowners")
async def listowners_text(ctx):
    if not is_owner_id(ctx.author.id):
        return await ctx.send(embed=build_denied_embed())
    lines = [f"<@{uid}> ({'env' if uid in _ENV_OWNER_IDS else 'added'})" for uid in sorted(all_owner_ids())]
    embed = discord.Embed(title="👑 Server Owners", description="\n".join(lines), color=COLOR_INFO)
    await ctx.send(embed=footer(embed))


# ─────────────────────────────────────────────────────────────
# Slash commands
# ─────────────────────────────────────────────────────────────

@bot.tree.command(name="ping", description="Shows bot latency")
async def ping_slash(interaction: discord.Interaction):
    await interaction.response.send_message(f"🏓 Pong! {round(bot.latency * 1000)}ms")


@bot.tree.command(name="serverstatus", description="Show live Minecraft server status")
async def serverstatus_slash(interaction: discord.Interaction):
    await interaction.response.defer()
    mc_status = await get_mc_status()
    await interaction.followup.send(embed=build_status_embed(mc_status))


@bot.tree.command(name="status", description="Show panel resource usage (RAM/CPU)")
async def status_slash(interaction: discord.Interaction):
    await interaction.response.defer()
    result, http_status = await fetch_resources()
    if result is None:
        return await interaction.followup.send(
            embed=build_error_embed("Status Error", f"HTTP {http_status}")
        )
    state, ram, cpu = result
    await interaction.followup.send(embed=build_resource_embed(state, ram, cpu))


@bot.tree.command(name="panel", description="Open the server control panel")
async def panel_slash(interaction: discord.Interaction):
    embed = discord.Embed(
        title=f"🎛️ {BRAND_NAME} Control Panel",
        description="Owner-only controls. Buttons are visible to everyone but only work for owners.",
        color=COLOR_INFO,
    )
    await interaction.response.send_message(embed=footer(embed), view=ControlPanelView())


@bot.tree.command(name="start", description="Start the Minecraft server")
async def start_slash(interaction: discord.Interaction):
    if not is_owner_id(interaction.user.id):
        return await interaction.response.send_message(embed=build_denied_embed(), ephemeral=True)
    await interaction.response.defer()
    ok, err = await do_power_action("start")
    await interaction.followup.send("🟢 Server starting!" if ok else f"❌ {err}")


@bot.tree.command(name="stop", description="Stop the Minecraft server")
async def stop_slash(interaction: discord.Interaction):
    if not is_owner_id(interaction.user.id):
        return await interaction.response.send_message(embed=build_denied_embed(), ephemeral=True)
    await interaction.response.defer()
    ok, err = await do_power_action("stop")
    await interaction.followup.send("🔴 Server stopping!" if ok else f"❌ {err}")


@bot.tree.command(name="restart", description="Restart the Minecraft server")
async def restart_slash(interaction: discord.Interaction):
    if not is_owner_id(interaction.user.id):
        return await interaction.response.send_message(embed=build_denied_embed(), ephemeral=True)
    await interaction.response.defer()
    ok, err = await do_power_action("restart")
    await interaction.followup.send("🔄 Server restarting!" if ok else f"❌ {err}")


@bot.tree.command(name="cmd", description="Send a command to the Minecraft server console")
@app_commands.describe(command="The console command to run")
async def cmd_slash(interaction: discord.Interaction, command: str):
    if not is_owner_id(interaction.user.id):
        return await interaction.response.send_message(embed=build_denied_embed(), ephemeral=True)
    await interaction.response.defer()
    ok, err = await do_console_command(command)
    await interaction.followup.send(f"✅ Executed: `{command}`" if ok else f"❌ {err}")


@bot.tree.command(name="addowner", description="Grant a user owner permissions")
@app_commands.describe(user="The user to add as an owner")
async def addowner_slash(interaction: discord.Interaction, user: discord.Member):
    if not is_owner_id(interaction.user.id):
        return await interaction.response.send_message(embed=build_denied_embed(), ephemeral=True)
    if add_owner(user.id):
        await interaction.response.send_message(f"✅ {user.mention} is now an owner.")
    else:
        await interaction.response.send_message(f"⚠️ {user.mention} is already an owner.", ephemeral=True)


@bot.tree.command(name="removeowner", description="Revoke a user's owner permissions")
@app_commands.describe(user="The user to remove as an owner")
async def removeowner_slash(interaction: discord.Interaction, user: discord.Member):
    if not is_owner_id(interaction.user.id):
        return await interaction.response.send_message(embed=build_denied_embed(), ephemeral=True)
    ok, err = remove_owner(user.id)
    if ok:
        await interaction.response.send_message(f"✅ {user.mention} removed as owner.")
    else:
        await interaction.response.send_message(f"❌ {err}", ephemeral=True)


@bot.tree.command(name="listowners", description="List all current server owners")
async def listowners_slash(interaction: discord.Interaction):
    if not is_owner_id(interaction.user.id):
        return await interaction.response.send_message(embed=build_denied_embed(), ephemeral=True)
    lines = [f"<@{uid}> ({'env' if uid in _ENV_OWNER_IDS else 'added'})" for uid in sorted(all_owner_ids())]
    embed = discord.Embed(title="👑 Server Owners", description="\n".join(lines), color=COLOR_INFO)
    await interaction.response.send_message(embed=footer(embed), ephemeral=True)


# ─────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────

async def main():
    async with bot:
        await bot.start(TOKEN)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        # ptero.session is closed inside the bot's own lifecycle in on_ready-created
        # session, but guard here in case start() never fired.
        if ptero.session and not ptero.session.closed:
            asyncio.run(ptero.close())
