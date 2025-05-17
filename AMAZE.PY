import discord
from discord.ext import commands
import os
import re
from datetime import datetime, timedelta
from collections import defaultdict

# ==================== BOT SETUP ====================
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# ==================== DATA STRUCTURES ====================
# Per-guild settings: owner ID and log channel ID
guild_owners = {}   # guild_id : owner_user_id
guild_log_channels = {}  # guild_id : log_channel_id

# Whitelisted users per guild: guild_id : set(user_ids)
whitelisted = defaultdict(set)

# Timeout duration for mass mentions (seconds)
TIMEOUT_DURATION = 600

# Regex to detect links
LINK_PATTERN = re.compile(r"(https?://\S+|www\.\S+)")

# Rate limit tracking (user_id: last_action_time)
rate_limit_cache = defaultdict(lambda: datetime.min)

# ==================== HELPERS ====================
def can_take_action(guild_id, user_id):
    # Check if user is NOT owner or whitelisted for that guild
    if guild_id not in guild_owners:
        return False
    if user_id == guild_owners[guild_id]:
        return False
    if user_id in whitelisted[guild_id]:
        return False
    return True

async def punish_user(guild, user, reason):
    """Ban the user with reason."""
    try:
        await guild.ban(user, reason=reason)
    except Exception as e:
        print(f"Failed to ban {user} in guild {guild.id}: {e}")

async def log_action(guild, message):
    if guild.id not in guild_log_channels:
        print(f"Log channel not set for guild {guild.id}")
        return
    log_channel_id = guild_log_channels[guild.id]
    channel = guild.get_channel(log_channel_id)
    if channel:
        embed = discord.Embed(
            description=message,
            color=discord.Color.orange(),
            timestamp=datetime.utcnow()
        )
        try:
            await channel.send(embed=embed)
        except Exception as e:
            print(f"Failed to send log message in guild {guild.id}: {e}")

async def send_dm(user, message):
    try:
        await user.send(f"🔔 **Server Security Alert** 🔔\n{message}")
    except discord.Forbidden:
        print(f"Could not DM {user}")

def is_rate_limited(user_id, cooldown_seconds=10):
    last_action = rate_limit_cache[user_id]
    now = datetime.utcnow()
    if (now - last_action).total_seconds() < cooldown_seconds:
        return True
    rate_limit_cache[user_id] = now
    return False

# ==================== EVENTS ====================
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.watching,
        name="for rule breakers"
    ))

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    guild_id = message.guild.id if message.guild else None
    if guild_id is None:
        await bot.process_commands(message)
        return

    # Link protection
    if LINK_PATTERN.search(message.content) and can_take_action(guild_id, message.author.id):
        try:
            await message.delete()
        except Exception:
            pass
        await send_dm(message.author, "🚫 **Links are not allowed in this server!**")
        await log_action(message.guild,
            f"❌ {message.author.mention} tried to send a link in {message.channel.mention}.\n"
            f"**Message:** {message.content[:100]}..."
        )

    # Mass mention protection
    if any(mention in message.content.lower() for mention in ["@everyone", "@here"]) and can_take_action(guild_id, message.author.id):
        try:
            await message.delete()
        except Exception:
            pass
        try:
            await message.author.timeout(timedelta(seconds=TIMEOUT_DURATION), reason="Mass mention violation")
        except Exception:
            pass
        await send_dm(message.author, 
            f"⏳ **You have been timed out for {TIMEOUT_DURATION//60} minutes**\nReason: Mentioning @everyone or @here without permission"
        )
        await log_action(message.guild,
            f"⚠️ {message.author.mention} was timed out for mass mentions in {message.channel.mention}"
        )

    await bot.process_commands(message)

# ===== CHANNEL CREATE/DELETE EVENTS =====
@bot.event
async def on_guild_channel_create(channel):
    guild = channel.guild
    try:
        entry = await guild.audit_logs(action=discord.AuditLogAction.channel_create, limit=5).find(lambda e: e.target.id == channel.id)
        if entry and can_take_action(guild.id, entry.user.id):
            if is_rate_limited(entry.user.id):
                return
            await channel.delete()
            await punish_user(guild, entry.user, "Unauthorized channel creation")
            await log_action(guild, f"🚨 {entry.user.mention} banned for creating channel #{channel.name}")
    except Exception as e:
        print(f"Error in on_guild_channel_create: {e}")

@bot.event
async def on_guild_channel_delete(channel):
    guild = channel.guild
    try:
        entry = await guild.audit_logs(action=discord.AuditLogAction.channel_delete, limit=5).find(lambda e: e.target.id == channel.id)
        if entry and can_take_action(guild.id, entry.user.id):
            if is_rate_limited(entry.user.id):
                return
            await punish_user(guild, entry.user, "Unauthorized channel deletion")
            await log_action(guild, f"🚨 {entry.user.mention} banned for deleting channel #{channel.name}")
    except Exception as e:
        print(f"Error in on_guild_channel_delete: {e}")

# ===== ROLE CREATE/DELETE EVENTS =====
@bot.event
async def on_guild_role_create(role):
    guild = role.guild
    try:
        entry = await guild.audit_logs(action=discord.AuditLogAction.role_create, limit=5).find(lambda e: e.target.id == role.id)
        if entry and can_take_action(guild.id, entry.user.id):
            if is_rate_limited(entry.user.id):
                return
            await role.delete()
            await punish_user(guild, entry.user, "Unauthorized role creation")
            await log_action(guild, f"🚨 {entry.user.mention} banned for creating role @{role.name}")
    except Exception as e:
        print(f"Error in on_guild_role_create: {e}")

@bot.event
async def on_guild_role_delete(role):
    guild = role.guild
    try:
        entry = await guild.audit_logs(action=discord.AuditLogAction.role_delete, limit=5).find(lambda e: e.target.id == role.id)
        if entry and can_take_action(guild.id, entry.user.id):
            if is_rate_limited(entry.user.id):
                return
            await punish_user(guild, entry.user, "Unauthorized role deletion")
            await log_action(guild, f"🚨 {entry.user.mention} banned for deleting role @{role.name}")
    except Exception as e:
        print(f"Error in on_guild_role_delete: {e}")

# ===== MEMBER BAN EVENT (Unauthorized ban attempts) =====
@bot.event
async def on_member_ban(guild, user):
    try:
        entry = await guild.audit_logs(action=discord.AuditLogAction.ban, limit=5).find(lambda e: e.target.id == user.id)
        if entry and can_take_action(guild.id, entry.user.id):
            if is_rate_limited(entry.user.id):
                return
            await punish_user(guild, entry.user, f"Unauthorized ban attempt on {user}")
            await log_action(guild, f"🚨 {entry.user.mention} banned for trying to ban {user.mention}")
    except Exception as e:
        print(f"Error in on_member_ban: {e}")

# ==================== COMMANDS ====================
@bot.command()
@commands.is_owner()
async def setowner(ctx, member: discord.Member):
    guild_owners[ctx.guild.id] = member.id
    await ctx.send(f"✅ Owner for this server set to {member.mention}")

@bot.command()
@commands.is_owner()
async def setlog(ctx, channel: discord.TextChannel):
    guild_log_channels[ctx.guild.id] = channel.id
    await ctx.send(f"✅ Log channel set to {channel.mention}")

@bot.command()
@commands.is_owner()
async def whitelist(ctx, member: discord.Member):
    whitelisted[ctx.guild.id].add(member.id)
    await ctx.send(f"✅ {member.mention} has been whitelisted.")
    await log_action(ctx.guild, f"📝 {ctx.author.mention} whitelisted {member.mention}")

@bot.command()
@commands.is_owner()
async def unwhitelist(ctx, member: discord.Member):
    whitelisted[ctx.guild.id].discard(member.id)
    await ctx.send(f"❌ {member.mention} has been removed from whitelist.")
    await log_action(ctx.guild, f"📝 {ctx.author.mention} unwhitelisted {member.mention}")

# ==================== ERROR HANDLING ====================
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("❌ Missing required arguments!")
    elif isinstance(error, commands.NotOwner):
        await ctx.send("🚫 This command is for bot owners only!")
    else:
        await log_action(ctx.guild, f"⚠️ Error in command `{ctx.command}`: {str(error)}")
        raise error

# ==================== RUN BOT ====================
if __name__ == "__main__":
    TOKEN = os.getenv("DISCORD_TOKEN")
    if not TOKEN:
        print("❌ DISCORD_TOKEN is not set in environment variables!")
    else:
        try:
            bot.run(TOKEN)
        except discord.LoginFailure:
            print("Invalid Discord token. Please check your environment variables.")
        except Exception as e:
            print(f"An error occurred: {e}")
