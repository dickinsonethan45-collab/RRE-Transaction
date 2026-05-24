import discord
from discord import app_commands
from discord.ext import commands
import json
import os
from datetime import datetime, timezone

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")

def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

cfg = load_config()

BOT_TOKEN       = cfg.get("BotToken", "")
OWNERS          = cfg.get("OwnerIDs", [])
MOD_CHANNEL_ID  = cfg.get("ModChannelID", 0)
SUPPORTER_ROLE  = cfg.get("SupporterRoleName", "Supporter")
PAYPAL_ME       = cfg.get("PayPalMe", "")
CASHAPP         = cfg.get("CashApp", "")
REQUIRED_AMOUNT = cfg.get("RequiredAmount", 30)
STAFF_ROLE_ID   = cfg.get("StaffRoleID", 0)

intents = discord.Intents.default()
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

pending: dict[int, dict] = {}
approved: set[int] = set()
used_transaction_ids: set[tuple[str, str]] = set()  # (method, transaction_id) pairs


def is_staff(interaction: discord.Interaction) -> bool:
    if interaction.user.id in OWNERS:
        return True
    if interaction.guild:
        if interaction.user.guild_permissions.administrator:
            return True
        if STAFF_ROLE_ID:
            return any(r.id == STAFF_ROLE_ID for r in interaction.user.roles)
    return False


@bot.event
async def on_ready():
    # Set default permissions: /approve /deny /pending /checkstatus visible to staff role only
    guild_obj = discord.Object(id=0)  # placeholder, handled per guild below
    for guild in bot.guilds:
        await tree.sync(guild=guild)

    await tree.sync()
    print(f"✅ Logged in as {bot.user} | Commands synced")


@bot.event
async def on_interaction(interaction: discord.Interaction):
    if interaction.type != discord.InteractionType.component:
        return
    custom_id = interaction.data.get("custom_id", "")
    if not (custom_id.startswith("approve_") or custom_id.startswith("deny_")):
        return

    if not is_staff(interaction):
        await interaction.response.send_message("❌ No permission.", ephemeral=True)
        return

    target_id = int(custom_id.split("_", 1)[1])
    try:
        target = interaction.guild.get_member(target_id) or await interaction.guild.fetch_member(target_id)
    except Exception:
        await interaction.response.send_message("❌ Could not find that user.", ephemeral=True)
        return

    if custom_id.startswith("approve_"):
        await _do_approve(interaction, target, interaction.guild, via_button=True)
    else:
        await _do_deny(interaction, target, "Your transaction could not be verified. Contact staff if you think this is a mistake.", via_button=True)


async def _do_approve(interaction, target, guild, via_button=False):
    if target is None or target.id not in pending:
        await interaction.response.send_message("❌ No pending request for that user.", ephemeral=True)
        return

    role = discord.utils.get(guild.roles, name=SUPPORTER_ROLE)
    if not role:
        await interaction.response.send_message(f"❌ Role \"{SUPPORTER_ROLE}\" not found.", ephemeral=True)
        return

    data = pending.pop(target.id)
    approved.add(target.id)
    used_transaction_ids.add((data["method"], data["transaction_id"].strip().lower()))
    await target.add_roles(role, reason="Supporter payment verified")

    if via_button and interaction.message:
        orig = interaction.message.embeds[0]
        updated = discord.Embed(title="✅ Approved — Role Granted", color=0x2ECC71, timestamp=datetime.now(timezone.utc))
        for f in orig.fields:
            updated.add_field(name=f.name, value=f.value, inline=f.inline)
        updated.set_footer(text=f"Approved by {interaction.user} • {datetime.now(timezone.utc).strftime('%d/%m/%Y %H:%M')}")
        await interaction.response.edit_message(embed=updated, view=None)
    else:
        await interaction.response.send_message(f"✅ Granted **{SUPPORTER_ROLE}** to {target.mention}.", ephemeral=True)

    try:
        embed = discord.Embed(
            title="🎉 You're now a Supporter!",
            description=f"Your payment was verified and you've been given the **{SUPPORTER_ROLE}** role. Thank you so much for your support! 💛",
            color=0x2ECC71,
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="Transaction ID", value=f"`{data['transaction_id']}`")
        await target.send(embed=embed)
    except discord.Forbidden:
        pass


async def _do_deny(interaction, target, reason, via_button=False):
    if target is None or target.id not in pending:
        await interaction.response.send_message("❌ No pending request for that user.", ephemeral=True)
        return

    data = pending.pop(target.id)
    used_transaction_ids.add((data["method"], data["transaction_id"].strip().lower()))

    if via_button and interaction.message:
        orig = interaction.message.embeds[0]
        updated = discord.Embed(title="❌ Denied", color=0xE74C3C, timestamp=datetime.now(timezone.utc))
        for f in orig.fields:
            updated.add_field(name=f.name, value=f.value, inline=f.inline)
        updated.set_footer(text=f"Denied by {interaction.user} • {datetime.now(timezone.utc).strftime('%d/%m/%Y %H:%M')}")
        await interaction.response.edit_message(embed=updated, view=None)
    else:
        await interaction.response.send_message(f"❌ Denied request for {target.mention}.", ephemeral=True)

    try:
        embed = discord.Embed(
            title="❌ Verification Denied",
            description=f"Your Supporter verification was denied.\n\n**Reason:** {reason}\n\nContact staff if you think this is a mistake.",
            color=0xE74C3C,
            timestamp=datetime.now(timezone.utc),
        )
        await target.send(embed=embed)
    except discord.Forbidden:
        pass


# ── Public Commands ───────────────────────────────────────────────────────────

@tree.command(name="pay", description="Get payment instructions to receive the Supporter role")
async def pay_command(interaction: discord.Interaction):
    user = interaction.user
    embed = discord.Embed(
        title="💳 Get the Supporter Role",
        description=f"Follow the steps below to get the **{SUPPORTER_ROLE}** role for **${REQUIRED_AMOUNT}**.",
        color=0xF1C40F,
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(
        name="Step 1 — Choose a Payment Method",
        value=(
            f"💰 **PayPal:** [Click here to pay](https://paypal.me/{PAYPAL_ME}/{REQUIRED_AMOUNT})\n"
            f"💵 **CashApp:** `${CASHAPP}`"
        ),
        inline=False,
    )
    embed.add_field(
        name="Step 2 — Add Your Username to the Note",
        value=(
            f"In the **note/memo** field, write exactly:\n"
            f"`{user.name} - Supporter`\n"
            f"⚠️ This is how we verify it's you. Don't skip this step."
        ),
        inline=False,
    )
    embed.add_field(
        name="Step 3 — Get Your Transaction ID",
        value=(
            "**PayPal:** Go to Activity → click the payment → copy the **Transaction ID**\n"
            "**CashApp:** Tap the payment → tap `...` → copy the **Transaction ID**"
        ),
        inline=False,
    )
    embed.add_field(
        name="Step 4 — Submit with /verify",
        value="Run `/verify` in this server, paste your transaction ID, and select your payment method. Staff will review shortly.",
        inline=False,
    )
    embed.set_footer(text=f"Sending ${REQUIRED_AMOUNT} without your username in the note means we cannot verify your payment.")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@tree.command(name="verify", description="Submit your transaction ID after paying for Supporter")
@app_commands.describe(transaction_id="Your PayPal transaction ID or CashApp payment note", method="How did you pay?")
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
        await interaction.response.send_message("⏳ You already have a pending request. Wait for staff to review it.", ephemeral=True)
        return

    tid_lower = transaction_id.strip().lower()
    tid_key = (method.value, tid_lower)
    if tid_key in used_transaction_ids:
        await interaction.response.send_message(
            "❌ That transaction ID has already been used. If you think this is a mistake, contact staff.",
            ephemeral=True
        )
        return
    if any(d["method"] == method.value and d["transaction_id"].lower() == tid_lower for d in pending.values()):
        await interaction.response.send_message(
            "❌ That transaction ID is already pending review. If this is yours, please wait for staff to action it.",
            ephemeral=True
        )
        return

    if not MOD_CHANNEL_ID:
        await interaction.response.send_message("❌ Mod channel not set up. Contact an admin.", ephemeral=True)
        return

    try:
        mod_channel = bot.get_channel(MOD_CHANNEL_ID) or await bot.fetch_channel(MOD_CHANNEL_ID)
    except Exception:
        await interaction.response.send_message("❌ Could not find mod channel. Contact an admin.", ephemeral=True)
        return

    method_label = "💰 PayPal" if method.value == "paypal" else "💵 CashApp"

    embed = discord.Embed(title="🔔 New Supporter Verification", color=0xF1C40F, timestamp=datetime.now(timezone.utc))
    embed.add_field(name="User", value=f"{user.mention} (`{user.name}`)", inline=True)
    embed.add_field(name="User ID", value=str(user.id), inline=True)
    embed.add_field(name="Method", value=method_label, inline=True)
    embed.add_field(name="Transaction ID / Note", value=f"`{transaction_id}`", inline=False)
    embed.set_thumbnail(url=user.display_avatar.url)
    embed.set_footer(text="Use the buttons or /approve / /deny to action this.")

    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label="✅ Approve", style=discord.ButtonStyle.success, custom_id=f"approve_{user.id}"))
    view.add_item(discord.ui.Button(label="❌ Deny", style=discord.ButtonStyle.danger, custom_id=f"deny_{user.id}"))

    mod_msg = await mod_channel.send(embed=embed, view=view)

    pending[user.id] = {
        "transaction_id": transaction_id,
        "method": method.value,
        "timestamp": datetime.now(timezone.utc),
        "message_id": mod_msg.id,
    }

    await interaction.response.send_message(
        f"✅ **Submitted!** Your request has been sent to staff.\nTransaction ID: `{transaction_id}`\n\nYou'll get a DM once reviewed.",
        ephemeral=True,
    )


@tree.command(name="checkstatus", description="Check if your Supporter verification is still pending")
async def checkstatus_command(interaction: discord.Interaction):
    user = interaction.user
    if user.id in approved:
        await interaction.response.send_message("✅ Your payment has been approved — you have the Supporter role!", ephemeral=True)
        return
    if user.id in pending:
        data = pending[user.id]
        method = "PayPal" if data["method"] == "paypal" else "CashApp"
        ts = int(data["timestamp"].timestamp())
        embed = discord.Embed(
            title="⏳ Your Request is Pending",
            description="Staff haven't reviewed your request yet. Please be patient.",
            color=0xF1C40F,
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="Method", value=method, inline=True)
        embed.add_field(name="Transaction ID", value=f"`{data['transaction_id']}`", inline=True)
        embed.add_field(name="Submitted", value=f"<t:{ts}:R>", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    await interaction.response.send_message(
        f"❌ You have no pending request. Run `/pay` to get started.",
        ephemeral=True,
    )


# ── Staff Only Commands ───────────────────────────────────────────────────────

@tree.command(name="approve", description="(Staff) Approve a pending Supporter request")
@app_commands.describe(user="The user to approve")
async def approve_command(interaction: discord.Interaction, user: discord.Member):
    if not is_staff(interaction):
        await interaction.response.send_message("❌ No permission.", ephemeral=True)
        return
    await _do_approve(interaction, user, interaction.guild)


@tree.command(name="deny", description="(Staff) Deny a pending Supporter request")
@app_commands.describe(user="The user to deny", reason="Reason sent to the user")
async def deny_command(interaction: discord.Interaction, user: discord.Member, reason: str = "No reason provided."):
    if not is_staff(interaction):
        await interaction.response.send_message("❌ No permission.", ephemeral=True)
        return
    await _do_deny(interaction, user, reason)


@tree.command(name="pending", description="(Staff) List all pending Supporter requests")
async def pending_command(interaction: discord.Interaction):
    if not is_staff(interaction):
        await interaction.response.send_message("❌ No permission.", ephemeral=True)
        return
    if not pending:
        await interaction.response.send_message("✅ No pending requests.", ephemeral=True)
        return
    lines = []
    for uid, data in pending.items():
        method = "PayPal" if data["method"] == "paypal" else "CashApp"
        ts = int(data["timestamp"].timestamp())
        lines.append(f"<@{uid}> — {method} — `{data['transaction_id']}` — <t:{ts}:R>")
    embed = discord.Embed(title=f"⏳ Pending Requests ({len(pending)})", description="\n".join(lines), color=0xE67E22)
    await interaction.response.send_message(embed=embed, ephemeral=True)


bot.run(BOT_TOKEN)
