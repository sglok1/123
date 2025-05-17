import discord
from discord.ext import commands
import os
from dotenv import load_dotenv

import re
from datetime import datetime, timedelta
from collections import defaultdict

# ==================== CONFIGURATION ====================
load_dotenv()

# Validate environment variables
def get_env_var(name, is_int=True):
    value = os.getenv(name)
    if not value:
        raise ValueError(f"{name} is not set in .env!")
    return int(value) if is_int else value

TOKEN = get_env_var("DISCORD_TOKEN", is_int=False)
OWNER_ID = get_env_var("OWNER_ID")
LOG_CHANNEL_ID = get_env_var("LOG_CHANNEL_ID")

# Bot setup
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# Security settings
whitelisted = {OWNER_ID}
LINK_PATTERN = re.compile(r"(https?://\S+|www\.\S+)")
ban_count = defaultdict(int)
TIMEOUT_DURATION = 600  # 10 minutes timeout for @everyone @here

# ==================== HELPER FUNCTIONS ====================
async def send_dm(user, message):
    """Sends a DM to the user."""
    try:
        await user.send(f"🔔 **Server Security Alert** 🔔\n{message}")
    except discord.Forbidden:
        print(f"Could not DM {user}")

async def log_action(guild, message):
    """Logs actions to a specific log channel."""
    log_channel = guild.get_channel(LOG_CHANNEL_ID)
    if log_channel:
        embed = discord.Embed(
            description=message,
            color=discord.Color.orange(),
            timestamp=datetime.utcnow()
        )
        await log_channel.send(embed=embed)

# ==================== BOT EVENTS ====================
@bot.event
async def on_ready():
    print(f'✅ Logged in as {bot.user} (ID: {bot.user.id})')
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.watching,
        name="for rule breakers"
    ))

@bot.event
async def on_message(message):
    """Message moderation system."""
    if message.author.bot:
        return

    # Link protection
    if LINK_PATTERN.search(message.content) and message.author.id not in whitelisted:
        await message.delete()
        await send_dm(message.author, "🚫 **Links are not allowed in this server!**")
        await log_action(message.guild, 
            f"❌ {message.author.mention} tried to send a link in {message.channel.mention}.\n"
            f"**Message:** {message.content[:100]}..."
        )

    # Mass mention protection
    if any(mention in message.content.lower() for mention in ["@everyone", "@here"]) and message.author.id not in whitelisted:
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

# ==================== SECURITY EVENTS ====================
@bot.event
async def on_guild_channel_create(channel):
    """Prevents unauthorized channel creation."""
    async for entry in channel.guild.audit_logs(action=discord.AuditLogAction.channel_create, limit=1):
        if entry.user.id not in whitelisted:
            await channel.delete()
            await entry.user.ban(reason="Unauthorized channel creation")
            await log_action(channel.guild,
                f"🚨 {entry.user.mention} was banned for creating channel #{channel.name}"
            )

@bot.event
async def on_guild_role_create(role):
    """Prevents unauthorized role creation."""
    async for entry in role.guild.audit_logs(action=discord.AuditLogAction.role_create, limit=1):
        if entry.user.id not in whitelisted:
            await role.delete()
            await entry.user.ban(reason="Unauthorized role creation")
            await log_action(role.guild,
                f"🚨 {entry.user.mention} was banned for creating role @{role.name}"
            )

@bot.event
async def on_member_ban(guild, user):
    """Prevents unauthorized bans."""
    async for entry in guild.audit_logs(action=discord.AuditLogAction.ban, limit=1):
        if entry.user.id not in whitelisted:
            await guild.ban(entry.user, reason="Unauthorized ban attempt")
            await log_action(guild,
                f"🚨 {entry.user.mention} was banned for trying to ban {user.mention}"
            )

# ==================== MODERATION COMMANDS ====================
@bot.command()
@commands.is_owner()
async def whitelist(ctx, member: discord.Member):
    """Adds a user to the whitelist."""
    whitelisted.add(member.id)
    await ctx.send(f"✅ {member.mention} has been whitelisted.")
    await log_action(ctx.guild,
        f"📝 {ctx.author.mention} whitelisted {member.mention}"
    )

@bot.command()
@commands.is_owner()
async def unwhitelist(ctx, member: discord.Member):
    """Removes a user from the whitelist."""
    whitelisted.discard(member.id)
    await ctx.send(f"❌ {member.mention} has been removed from whitelist.")
    await log_action(ctx.guild,
        f"📝 {ctx.author.mention} unwhitelisted {member.mention}"
    )

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
        await log_action(ctx.guild,
            f"⚠️ Error in command {ctx.command}: {str(error)}"
        )
        raise error

# ==================== RUN BOT ====================
if __name__ == "__main__":
    try:
        bot.run(TOKEN)
    except discord.LoginFailure:
        print("Invalid Discord token. Please check your .env file.")
    except Exception as e:
        print(f"An error occurred: {str(e)}")
