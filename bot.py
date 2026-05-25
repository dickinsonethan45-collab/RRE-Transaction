import discord
from discord import app_commands
from discord.ext import commands
import json
import os
from datetime import datetime, timezone

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")

def load_config(path=CONFIG_PATH):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

cfg = load_config()

BOT_TOKEN          = cfg.get("BotToken", "")
OWNERS             = cfg.get("OwnerIDs", [])
WELCOME_CHANNEL_ID = cfg.get("WelcomeChannelID", 0)
LEAVE_CHANNEL_ID   = cfg.get("LeaveChannelID", 0)
BANNER_URL         = cfg.get("BannerURL", "")
MOD_CHANNEL_ID     = cfg.get("ModChannelID", 0)
SUPPORTER_ROLE     = cfg.get("SupporterRoleName", "Supporter")
PAYPAL_ME          = cfg.get("PayPalMe", "")
CASHAPP            = cfg.get("CashApp", "")
REQUIRED_AMOUNT    = cfg.get("RequiredAmount", 30)

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# ── State ─────────────────────────────────────────────────────────────────────

# channel_id -> (message_content, sticky_message_id)
sticky_messages: dict[int, tuple[str, int]] = {}

# user_id -> { transaction_id, method, timestamp, message_id }
pending: dict[int, dict] = {}

# set of user_ids that have already been approved
approved: set[int] = set()


# ── Embeds ────────────────────────────────────────────────────────────────────

def make_welcome_embed(member: discord.Member) -> discord.Embed:
    embed = discord.Embed(
        title="🎉 Welcome!",
        description=f"Welcome To {member.guild.name}",
        color=0x2ECC71,
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="User", value=f"{member} ({member.id})", inline=True)
    embed.add_field(name="Member Count", value=str(member.guild.member_count), inline=True)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text=f"Welcome to {member.guild.name}! • {datetime.now(timezone.utc).strftime('%d/%m/%Y %H:%M')}")
    return embed


def make_leave_embed(member: discord.Member) -> discord.Embed:
    embed = discord.Embed(
        title=f"Goodbye {member}",
        description="Goodbye Hope To See You Again Soon",
        color=0xE67E22,
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="User", value=f"{member} ({member.id})", inline=True)
    embed.add_field(name="Member Count", value=str(member.guild.member_count), inline=True)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text=f"Goodbye from {member.guild.name}! • {datetime.now(timezone.utc).strftime('%d/%m/%Y %H:%M')}")
    return embed


# ── Events ────────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    guild = discord.Object(id=1508016021867462767)
    tree.copy_global_to(guild=guild)
    await tree.sync(guild=guild)
    await tree.sync()
    print(f"Logged in as {bot.user} | Commands synced")


@bot.event
async def on_member_join(member: discord.Member):
    if not WELCOME_CHANNEL_ID:
        return
    channel = member.guild.get_channel(WELCOME_CHANNEL_ID) or await bot.fetch_channel(WELCOME_CHANNEL_ID)
    if channel:
        await channel.send(embed=make_welcome_embed(member))


@bot.event
async def on_member_remove(member: discord.Member):
    if not LEAVE_CHANNEL_ID:
        return
    channel = member.guild.get_channel(LEAVE_CHANNEL_ID) or await bot.fetch_channel(LEAVE_CHANNEL_ID)
    if channel:
        await channel.send(embed=make_leave_embed(member))


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    channel_id = message.channel.id
    if channel_id in sticky_messages:
        content, old_id = sticky_messages[channel_id]
        try:
            old_msg = await message.channel.fetch_message(old_id)
            await old_msg.delete()
        except Exception:
            pass
        embed = discord.Embed(
            title="📌 Stickied Message:",
            description=content,
            color=0x5865F2,
        )
        sent = await message.channel.send(embed=embed)
        sticky_messages[channel_id] = (content, sent.id)
    await bot.process_commands(message)


@bot.event
async def on_interaction(interaction: discord.Interaction):
    """Handle approve/deny button clicks from the mod channel."""
    if interaction.type != discord.InteractionType.component:
        return

    custom_id = interaction.data.get("custom_id", "")
    if not (custom_id.startswith("approve_") or custom_id.startswith("deny_")):
        return

    # Must be owner or have Manage Roles
    is_owner = interaction.user.id in OWNERS
    has_perm = interaction.user.guild_permissions.manage_roles if interaction.guild else False
    if not (is_owner or has_perm):
        await interaction.response.send_message("❌ You don't have permission to do that.", ephemeral=True)
        return

    target_id = int(custom_id.split("_", 1)[1])
    target = interaction.guild.get_member(target_id) or await interaction.guild.fetch_member(target_id)

    if custom_id.startswith("approve_"):
        await _do_approve(interaction, target, interaction.guild, via_button=True)
    else:
        await _do_deny(
            interaction, target,
            "Your transaction could not be verified. Please contact staff if you believe this is an error.",
            via_button=True
        )


# ── Helpers ───────────────────────────────────────────────────────────────────

def is_staff(interaction: discord.Interaction) -> bool:
    if interaction.user.id in OWNERS:
        return True
    if interaction.guild:
        return interaction.user.guild_permissions.manage_roles
    return False


async def get_supporter_role(guild: discord.Guild) -> discord.Role | None:
    return discord.utils.get(guild.roles, name=SUPPORTER_ROLE)


async def _do_approve(
    interaction: discord.Interaction,
    target: discord.Member | None,
    guild: discord.Guild,
    via_button: bool = False,
):
    if target is None or target.id not in pending:
        await interaction.response.send_message(
            f"❌ No pending request found for that user.", ephemeral=True
        )
        return

    role = await get_supporter_role(guild)
    if not role:
        await interaction.response.send_message(
            f"❌ Role \"{SUPPORTER_ROLE}\" not found. Check config.json.", ephemeral=True
        )
        return

    data = pending.pop(target.id)
    approved.add(target.id)

    await target.add_roles(role, reason="Supporter payment verified")

    # Update the mod channel embed
    if via_button and interaction.message:
        original = interaction.message.embeds[0]
        updated = discord.Embed(
            title="✅ Approved — Supporter Role Granted",
            description=original.description,
            color=0x2ECC71,
            timestamp=datetime.now(timezone.utc),
        )
        for field in original.fields:
            updated.add_field(name=field.name, value=field.value, inline=field.inline)
        updated.set_footer(text=f"Approved by {interaction.user} • {datetime.now(timezone.utc).strftime('%d/%m/%Y %H:%M')}")
        await interaction.response.edit_message(embed=updated, view=None)
    else:
        await interaction.response.send_message(
            f"✅ Granted **{SUPPORTER_ROLE}** role to {target.mention}.", ephemeral=True
        )

    # DM the user
    try:
        dm_embed = discord.Embed(
            title="🎉 You're now a Supporter!",
            description=f"Your payment has been verified and you've been given the **{SUPPORTER_ROLE}** role. Thank you so much for your support! 💛",
            color=0x2ECC71,
            timestamp=datetime.now(timezone.utc),
        )
        dm_embed.add_field(name="Transaction ID", value=f"`{data['transaction_id']}`", inline=False)
        await target.send(embed=dm_embed)
    except discord.Forbidden:
        pass


async def _do_deny(
    interaction: discord.Interaction,
    target: discord.Member | None,
    reason: str,
    via_button: bool = False,
):
    if target is None or target.id not in pending:
        await interaction.response.send_message(
            "❌ No pending request found for that user.", ephemeral=True
        )
        return

    pending.pop(target.id)

    if via_button and interaction.message:
        original = interaction.message.embeds[0]
        updated = discord.Embed(
            title="❌ Denied",
            description=original.description,
            color=0xE74C3C,
            timestamp=datetime.now(timezone.utc),
        )
        for field in original.fields:
            updated.add_field(name=field.name, value=field.value, inline=field.inline)
        updated.set_footer(text=f"Denied by {interaction.user} • {datetime.now(timezone.utc).strftime('%d/%m/%Y %H:%M')}")
        await interaction.response.edit_message(embed=updated, view=None)
    else:
        await interaction.response.send_message(
            f"❌ Denied verification request for {target.mention}.", ephemeral=True
        )

    # DM the user
    try:
        dm_embed = discord.Embed(
            title="❌ Verification Denied",
            description=f"Your Supporter verification was denied.\n\n**Reason:** {reason}\n\nIf you believe this is a mistake, please contact staff directly.",
            color=0xE74C3C,
            timestamp=datetime.now(timezone.utc),
        )
        await target.send(embed=dm_embed)
    except discord.Forbidden:
        pass


# ── Original Commands ─────────────────────────────────────────────────────────

@tree.command(name="dm", description="Send a DM to a user")
@app_commands.describe(user="The user to DM", message="The message to send")
async def dm_command(interaction: discord.Interaction, user: discord.Member, message: str):
    if interaction.user.id not in OWNERS:
        await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
        return
    try:
        await user.send(message)
        embed = discord.Embed(
            description=f"✅ DM sent to **{user}**",
            color=0x2ECC71,
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="Message", value=message, inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message(f"❌ Could not DM {user} — they may have DMs disabled.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)


@tree.command(name="stickmessage", description="Stick a message to the bottom of this channel")
@app_commands.describe(message="The message to stick")
@app_commands.checks.has_permissions(manage_messages=True)
async def stickmessage_command(interaction: discord.Interaction, message: str):
    message = message.replace("\\n", "\n")
    channel_id = interaction.channel_id
    if channel_id in sticky_messages:
        try:
            old_msg = await interaction.channel.fetch_message(sticky_messages[channel_id][1])
            await old_msg.delete()
        except Exception:
            pass
    embed = discord.Embed(title="📌 Stickied Message:", description=message, color=0x5865F2)
    await interaction.response.send_message("✅ Message stickied!", ephemeral=True)
    sent = await interaction.channel.send(embed=embed)
    sticky_messages[channel_id] = (message, sent.id)


@tree.command(name="unstick", description="Remove the sticky message from this channel")
@app_commands.checks.has_permissions(manage_messages=True)
async def unstick_command(interaction: discord.Interaction):
    channel_id = interaction.channel_id
    if channel_id not in sticky_messages:
        await interaction.response.send_message("❌ No sticky message in this channel.", ephemeral=True)
        return
    try:
        old_msg = await interaction.channel.fetch_message(sticky_messages[channel_id][1])
        await old_msg.delete()
    except Exception:
        pass
    del sticky_messages[channel_id]
    await interaction.response.send_message("✅ Sticky message removed.", ephemeral=True)


# ── Supporter Commands ────────────────────────────────────────────────────────

@tree.command(name="pay", description="Get payment instructions to receive the Supporter role")
async def pay_command(interaction: discord.Interaction):
    user = interaction.user
    embed = discord.Embed(
        title="💳 Get the Supporter Role",
        description=(
            f"Send **${REQUIRED_AMOUNT}** to either payment method below.\n\n"
            f"⚠️ **IMPORTANT:** Include your Discord username (`{user.name}`) in the payment **note/memo**. "
            f"This is how we verify it's you."
        ),
        color=0xF1C40F,
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(
        name="💰 PayPal",
        value=f"[paypal.me/{PAYPAL_ME}](https://paypal.me/{PAYPAL_ME})\nNote: `{user.name} - Supporter`",
        inline=True,
    )
    embed.add_field(
        name="💵 CashApp",
        value=f"${CASHAPP}\nNote: `{user.name} - Supporter`",
        inline=True,
    )
    embed.add_field(
        name="📋 After Paying",
        value="Run `/verify` with your **PayPal transaction ID** or **CashApp payment note**. Staff will review and grant your role.",
        inline=False,
    )
    embed.set_footer(text="Do not send payment without including your username — we cannot verify it.")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@tree.command(name="verify", description="Submit your transaction ID after paying for Supporter")
@app_commands.describe(
    transaction_id="Your PayPal transaction ID or CashApp payment note",
    method="How did you pay?",
)
@app_commands.choices(method=[
    app_commands.Choice(name="PayPal", value="paypal"),
    app_commands.Choice(name="CashApp", value="cashapp"),
])
async def verify_command(interaction: discord.Interaction, transaction_id: str, method: app_commands.Choice[str]):
    user = interaction.user

    if user.id in approved:
        await interaction.response.send_message("✅ You already have the Supporter role!", ephemeral=True)
        return

    if user.id in pending:
        await interaction.response.send_message(
            "⏳ You already have a pending request. Please wait for staff to review it.", ephemeral=True
        )
        return

    if not MOD_CHANNEL_ID:
        await interaction.response.send_message("❌ Mod channel not configured. Contact an admin.", ephemeral=True)
        return

    mod_channel = bot.get_channel(MOD_CHANNEL_ID) or await bot.fetch_channel(MOD_CHANNEL_ID)
    if not mod_channel:
        await interaction.response.send_message("❌ Could not find mod channel. Contact an admin.", ephemeral=True)
        return

    method_label = "💰 PayPal" if method.value == "paypal" else "💵 CashApp"

    embed = discord.Embed(
        title="🔔 New Supporter Verification Request",
        color=0xF1C40F,
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="User", value=f"{user.mention} (`{user.name}`)", inline=True)
    embed.add_field(name="User ID", value=str(user.id), inline=True)
    embed.add_field(name="Method", value=method_label, inline=True)
    embed.add_field(name="Transaction ID / Note", value=f"`{transaction_id}`", inline=False)
    embed.set_thumbnail(url=user.display_avatar.url)
    embed.set_footer(text="Use the buttons or /approve / /deny to action this request.")

    # Approve / Deny buttons
    approve_btn = discord.ui.Button(
        label="✅ Approve",
        style=discord.ButtonStyle.success,
        custom_id=f"approve_{user.id}",
    )
    deny_btn = discord.ui.Button(
        label="❌ Deny",
        style=discord.ButtonStyle.danger,
        custom_id=f"deny_{user.id}",
    )
    view = discord.ui.View(timeout=None)
    view.add_item(approve_btn)
    view.add_item(deny_btn)

    mod_msg = await mod_channel.send(embed=embed, view=view)

    pending[user.id] = {
        "transaction_id": transaction_id,
        "method": method.value,
        "timestamp": datetime.now(timezone.utc),
        "message_id": mod_msg.id,
    }

    await interaction.response.send_message(
        f"✅ **Submitted!** Your request has been sent to staff.\n"
        f"Transaction ID: `{transaction_id}`\n\n"
        f"You'll receive a DM once it's reviewed (usually within 24 hours).",
        ephemeral=True,
    )


@tree.command(name="approve", description="(Staff) Approve a pending Supporter verification request")
@app_commands.describe(user="The user to approve")
async def approve_command(interaction: discord.Interaction, user: discord.Member):
    if not is_staff(interaction):
        await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
        return
    await _do_approve(interaction, user, interaction.guild)


@tree.command(name="deny", description="(Staff) Deny a pending Supporter verification request")
@app_commands.describe(user="The user to deny", reason="Reason sent to the user")
async def deny_command(interaction: discord.Interaction, user: discord.Member, reason: str = "No reason provided."):
    if not is_staff(interaction):
        await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
        return
    await _do_deny(interaction, user, reason)


@tree.command(name="pending", description="(Staff) List all pending Supporter verification requests")
async def pending_command(interaction: discord.Interaction):
    if not is_staff(interaction):
        await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
        return

    if not pending:
        await interaction.response.send_message("✅ No pending verification requests.", ephemeral=True)
        return

    lines = []
    for uid, data in pending.items():
        method = "PayPal" if data["method"] == "paypal" else "CashApp"
        ts = int(data["timestamp"].timestamp())
        lines.append(f"<@{uid}> — {method} — `{data['transaction_id']}` — <t:{ts}:R>")

    embed = discord.Embed(
        title=f"⏳ Pending Requests ({len(pending)})",
        description="\n".join(lines),
        color=0xE67E22,
        timestamp=datetime.now(timezone.utc),
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ── Run ───────────────────────────────────────────────────────────────────────


bot.run(BOT_TOKEN)
