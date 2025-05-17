import discord
from discord.ext import commands
import os
from dotenv import load_dotenv
import re
from datetime import datetime, timedelta
from collections import defaultdict

# ==================== CONFIGURATION ====================
load_dotenv()

def get_env_var(name, is_int=True):
    value = os.getenv(name)
    if not value:
        raise ValueError(f"{name} is not set in .env!")
    return int(value) if is_int else value

TOKEN = get_env_var("DISCORD_TOKEN", is_int=False)

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# Per guild config stored in memory; consider persistent storage for production
guild_configs = {}

# Regex to detect links
LINK_PATTERN = re.compile(r"(https?://\S+|www\.\S+)")

# Rate limits for sensitive actions (user_id: [timestamps])
rate_limits = {
    'ban': defaultdict(list),
    'role_create': defaultdict(list),
    'role_delete': defaultdict(list),
    'channel_create': defaultdict(list),
    'channel_delete': defaultdict(list),
    'role_assign': defaultdict(list),
}

RATE_LIMIT_MAX = 3  # max allowed actions per minute per type
RATE_LIMIT_INTERVAL = 60  # seconds

# Timeout duration for mass mention
TIMEOUT_DURATION = 600

# ==================== Helper functions ====================
def get_guild_config(guild_id):
    return guild_configs.setdefault(guild_id, {
        "owner_id": None,
        "whitelisted_ids": set(),
        "log_channel_id": None,
    })

def update_guild_config(guild_id, updates):
    config = get_guild_config(guild_id)
    for k, v in updates.items():
        if k == "whitelisted_ids":
            config[k] = set(v)
        else:
            config[k] = v

def is_whitelisted(guild_id, user_id):
    config = get_guild_config(guild_id)
    return user_id == config.get("owner_id") or user_id in config.get("whitelisted_ids", set())

def can_take_action(guild_id, user_id):
    """Check if user is not whitelisted (can be punished)"""
    return not is_whitelisted(guild_id, user_id)

async def send_dm(user, message):
    try:
        await user.send(f"🔔 **Server Security Alert** 🔔\n{message}")
    except discord.Forbidden:
        print(f"Could not DM {user}")

async def log_action(guild, message):
    config = get_guild_config(guild.id)
    log_channel_id = config.get("log_channel_id")
    if not log_channel_id:
        return
    log_channel = guild.get_channel(log_channel_id)
    if log_channel:
        embed = discord.Embed(
            description=message,
            color=discord.Color.orange(),
            timestamp=datetime.utcnow()
        )
        await log_channel.send(embed=embed)

def cleanup_old_actions(user_id, action_type):
    now = datetime.utcnow().timestamp()
    rate_limits[action_type][user_id] = [
        ts for ts in rate_limits[action_type][user_id]
        if now - ts < RATE_LIMIT_INTERVAL
    ]

def add_action(user_id, action_type):
    now = datetime.utcnow().timestamp()
    cleanup_old_actions(user_id, action_type)
    rate_limits[action_type][user_id].append(now)
    return len(rate_limits[action_type][user_id])

async def punish_user(guild, user, reason):
    if can_take_action(guild.id, user.id):
        try:
            await guild.ban(user, reason=reason)
            await log_action(guild, f"🚨 {user.mention} was banned. Reason: {reason}")
        except Exception as e:
            print(f"Failed to ban {user}: {e}")

# ==================== BOT EVENTS ====================

@bot.event
async def on_ready():
    print(f'✅ Logged in as {bot.user} (ID: {bot.user.id})')
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.watching,
        name="for nukers"
    ))

# Ban both bot and the adder if bot gets added (requires bot to be invited via OAuth2)
@bot.event
async def on_guild_join(guild):
    # Discord does not provide who invited the bot directly,
    # but we can check audit logs for bot addition.
    async for entry in guild.audit_logs(action=discord.AuditLogAction.bot_add, limit=5):
        if entry.target.id == bot.user.id:
            invitor = entry.user
            if invitor.id != bot.user.id:  # Just in case bot added itself (unlikely)
                try:
                    await guild.ban(invitor, reason="Unauthorized bot addition")
                    await guild.ban(bot.user, reason="Bot banned due to unauthorized addition")
                    await log_action(guild, f"🚨 {invitor.mention} banned for unauthorized bot addition. Bot banned as well.")
                except Exception as e:
                    print(f"Failed to ban invitor or bot: {e}")
            break

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    guild_id = message.guild.id if message.guild else None
    if not guild_id:
        return

    config = get_guild_config(guild_id)

    # Link protection
    if LINK_PATTERN.search(message.content) and not is_whitelisted(guild_id, message.author.id):
        await message.delete()
        await send_dm(message.author, "🚫 **Links are not allowed in this server!**")
        await log_action(message.guild,
            f"❌ {message.author.mention} tried to send a link in {message.channel.mention}.\n"
            f"**Message:** {message.content[:100]}..."
        )

    # Mass mention protection
    if any(mention in message.content.lower() for mention in ["@everyone", "@here"]) and not is_whitelisted(guild_id, message.author.id):
        await message.delete()
        await message.author.timeout(
            timedelta(seconds=TIMEOUT_DURATION),
            reason="Mass mention violation"
        )
        await send_dm(message.author,
            f"⏳ **You have been timed out for {TIMEOUT_DURATION//60} minutes**\n"
            "Reason: Mentioning @everyone or @here without permission"
        )
        await log_action(message.guild,
            f"⚠️ {message.author.mention} was timed out for mass mentions in {message.channel.mention}"
        )

    await bot.process_commands(message)

# ==================== AUDIT LOG EVENTS ====================

async def rate_limit_check(guild, user, action_type):
    count = add_action(user.id, action_type)
    if count > RATE_LIMIT_MAX:
        await punish_user(guild, user, f"Exceeded rate limit for {action_type}")
        return True
    return False

@bot.event
async def on_guild_channel_create(channel):
    async for entry in channel.guild.audit_logs(action=discord.AuditLogAction.channel_create, limit=1):
        if can_take_action(channel.guild.id, entry.user.id):
            # Rate limit check
            if await rate_limit_check(channel.guild, entry.user, 'channel_create'):
                await channel.delete()
                return
            # Immediate punishment if unauthorized
            await channel.delete()
            await punish_user(channel.guild, entry.user, "Unauthorized channel creation")
            await log_action(channel.guild,
                f"🚨 {entry.user.mention} banned for creating channel #{channel.name}"
            )

@bot.event
async def on_guild_channel_delete(channel):
    async for entry in channel.guild.audit_logs(action=discord.AuditLogAction.channel_delete, limit=1):
        if can_take_action(channel.guild.id, entry.user.id):
            # Rate limit check
            await rate_limit_check(channel.guild, entry.user, 'channel_delete')
            # No auto punish here (optional)

@bot.event
async def on_guild_role_create(role):
    async for entry in role.guild.audit_logs(action=discord.AuditLogAction.role_create, limit=1):
        if can_take_action(role.guild.id, entry.user.id):
            if await rate_limit_check(role.guild, entry.user, 'role_create'):
                await role.delete()
                return
            await role.delete()
            await punish_user(role.guild, entry.user, "Unauthorized role creation")
            await log_action(role.guild,
                f"🚨 {entry.user.mention} banned for creating role @{role.name}"
            )

@bot.event
async def on_guild_role_delete(role):
    async for entry in role.guild.audit_logs(action=discord.AuditLogAction.role_delete, limit=1):
        if can_take_action(role.guild.id, entry.user.id):
            await rate_limit_check(role.guild, entry.user, 'role_delete')
            # Optionally restore role here if you want (code for restore omitted for brevity)

@bot.event
async def on_guild_member_update(before, after):
    # Detect role additions by unauthorized users
    guild_id = before.guild.id
    config = get_guild_config(guild_id)
    if can_take_action(guild_id, after.guild.me.id):  # Just making sure bot can act
    
        if before.roles != after.roles:
            added_roles = set(after.roles) - set(before.roles)
            if added_roles:
                async for entry in after.guild.audit_logs(action=discord.AuditLogAction.member_role_update, limit=1):
                    if entry.target.id == after.id and entry.user.id != after.id:
                        # Someone else gave the role
                        if can_take_action(guild_id, entry.user.id):
                            # Kick the person who gave the role, NOT the receiver
                            try:
                                await after.guild.kick(entry.user, reason="Unauthorized role assignment")
                                await log_action(after.guild,
                                    f"🚨 {entry.user.mention} kicked for unauthorized role assignment to {after.mention}"
                                )
                                await send_dm(entry.user, "You were kicked for unauthorized role assignment.")
                            except Exception as e:
                                print(f"Failed to kick role assigner: {e}")

# ==================== COMMANDS ====================

@bot.command()
async def setowner(ctx, member: discord.Member = None):
    if ctx.author.guild_permissions.administrator:
        member = member or ctx.author
        update_guild_config(ctx.guild.id, {"owner_id": member.id})
        await ctx.send(f"✅ Owner set to {member.mention}")
        await log_action(ctx.guild, f"📝 Owner set to {member.mention} by {ctx.author.mention}")
    else:
        await ctx.send("🚫 You must be an administrator to set the owner.")

@bot.command()
async def setlogchannel(ctx, channel: discord.TextChannel):
    config = get_guild_config(ctx.guild.id)
    if ctx.author.id != config.get("owner_id"):
        return await ctx.send("🚫 Only the owner can set the log channel.")
    update_guild_config(ctx.guild.id, {"log_channel_id": channel.id})
    await ctx.send(f"✅ Log channel set to {channel.mention}")

@bot.command()
async def whitelist(ctx, member: discord.Member):
    config = get_guild_config(ctx.guild.id)
    if ctx.author.id != config.get("owner_id"):
        return await ctx.send("🚫 Only the owner can whitelist members.")
    whitelisted = config.get("whitelisted_ids", set())
    whitelisted.add(member.id)
    update_guild_config(ctx.guild.id, {"whitelisted_ids": list(whitelisted)})
    await ctx.send(f"✅ {member.mention} added to whitelist.")

@bot.command()
async def unwhitelist(ctx, member: discord.Member):
    config = get_guild_config(ctx.guild.id)
    if ctx.author.id != config.get("owner_id"):
        return await ctx.send("🚫 Only the owner can unwhitelist members.")
    whitelisted = config.get("whitelisted_ids", set())
    if member.id in whitelisted:
        whitelisted.remove(member.id)
        update_guild_config(ctx.guild.id, {"whitelisted_ids": list(whitelisted)})
        await ctx.send(f"❌ {member.mention} removed from whitelist.")
    else:
        await ctx.send(f"{member.mention} is not in whitelist.")

# ==================== ERROR HANDLING ====================
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("❌ Missing required arguments!")
    else:
        await log_action(ctx.guild, f"⚠️ Error in command `{ctx.command}`: {str(error)}")
        raise error

# ==================== RUN BOT ====================
bot.run(TOKEN)
