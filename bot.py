import os
import discord
import unicodedata
import re
import asyncio
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv
import json
from flask import Flask
from threading import Thread

# ====== Flask server pro uptime ======
app = Flask("")

@app.route("/")
def home():
    return "Bot is running"

def run_web():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

Thread(target=run_web).start()

# ====== NAČTENÍ TOKENU Z .env ======
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise ValueError("DISCORD_TOKEN není nastaven v .env")

# ====== ID SERVERU ======
GUILD_ID = 1455299174659522570  # nahraď svým ID serveru

# ====== INTENTY ======
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ====== STAV HRY ======
last_word = None
last_user_id = None
used_words = set()

# ====== KONFIGURACE ======
CONFIG_FILE = "config.json"

def load_config():
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {
            "listening_channel_id": None,
            "counting_channel_id": None,
            "last_number": None,
            "rating_channel_id": None,
            "points": {}
        }

def save_config(config):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=4)

config = load_config()
listening_channel_id = config.get("listening_channel_id")

# ====== FUNKCE PRO NORMALIZACI DIAKRITIKY ======
def normalize(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")

# ====== NAČTENÍ ČESKÉHO SLOVNÍKU ======
def load_czech_dictionary(path="czech.txt"):
    words = set()
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            word = line.strip().lower()
            if word.isalpha():
                words.add(normalize(word))
    return words

# ====== NAČTENÍ SLOVENSKÉHO SLOVNÍKU ======
def load_slovak_dictionary(path="sk.txt"):
    words = set()
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                word = line.strip().lower()
                if word.isalpha():
                    words.add(normalize(word))
    except FileNotFoundError:
        print(f"⚠️ Slovenský slovník {path} nenalezen!")
    return words


VALID_WORDS = load_czech_dictionary()
slovak_words = load_slovak_dictionary()
VALID_WORDS.update(slovak_words)  # spojíme oba slovníky

print(f"📚 Načteno {len(VALID_WORDS)} českých a slovenských slov")

# ====== FILTR SPROSTÝCH SLOV ======
RAW_BAD_WORDS = {
    "kurva", "kurvo", "do prdele", "prdel", "prdelka", "pica", "píča", "pico", "picus",
    "kunda", "kokot", "kokote", "curak", "čurák", "hovno", "hovna", "sračky", "mrdám",
    "sračka", "sracka", "jebat", "mrdat", "shit", "fuck",
    "debil", "blbec", "blbci", "kretén", "kretin", "krava", "kráva", "prase", "hovado",
    "hajzl", "hajzle", "cubra", "cubka", "cubko",
    "do pici", "do picture", "polib mi prdel", "vyliž si",
}
BAD_WORDS = {normalize(word) for word in RAW_BAD_WORDS}

# ====== BOT READY ======
@bot.event
async def on_ready():
    guild = discord.Object(id=GUILD_ID)
    bot.tree.copy_global_to(guild=guild)
    await bot.tree.sync(guild=guild)
    print(f"🤖 Přihlášen jako {bot.user}")

# ====== SLASH PŘÍKAZY ======
@bot.tree.command(
    name="set-listening-server",
    description="Nastaví aktuální kanál pro Slovní fotbal"
)
@app_commands.checks.has_permissions(administrator=True)
async def set_listening_server(interaction: discord.Interaction):
    global listening_channel_id, last_word, last_user_id, used_words, config
    listening_channel_id = interaction.channel.id
    last_word = None
    last_user_id = None
    used_words.clear()
    config["listening_channel_id"] = listening_channel_id
    save_config(config)
    await interaction.response.send_message(
        f"✅ Slovní fotbal nastaven v kanálu {interaction.channel.mention}",
        ephemeral=True
    )

@bot.tree.command(
    name="start-pocitani",
    description="Spustí hru Počítání v aktuálním kanálu"
)
@app_commands.checks.has_permissions(administrator=True)
async def start_pocitani(interaction: discord.Interaction):
    global config
    config["counting_channel_id"] = interaction.channel.id
    config["last_number"] = None
    save_config(config)
    await interaction.response.send_message(
        f"✅ Počítání spuštěno v kanálu {interaction.channel.mention}",
        ephemeral=True
    )

# ====== ON MESSAGE ======
@bot.event
async def on_message(message: discord.Message):
    global last_word, listening_channel_id, last_user_id, used_words, config

    if message.author.bot:
        return

    # ===== SLOVNÍ FOTBAL =====
    if listening_channel_id is not None and message.channel.id == listening_channel_id:
        content = message.content.strip().lower()
        normalized = normalize(content)

        # filtr sprostých slov
        if any(bad in normalized for bad in BAD_WORDS):
            await message.delete()
            await message.channel.send("🚫 Sprostá slova nejsou povolena!", delete_after=5)
            return

        # kontrola platnosti písmen
        if not content.replace(" ", "").isalpha():
            await message.delete()
            return

        # kontrola existujícího slova
        if normalized not in VALID_WORDS:
            await message.delete()
            await message.channel.send(f"❌ Slovo '{content}' neexistuje!", delete_after=5)
            return

        # kontrola, že nehraje dvakrát po sobě
        if last_user_id == message.author.id:
            await message.delete()
            await message.channel.send("❌ Počkej, až někdo jiný napíše slovo!", delete_after=5)
            return

        # první slovo
        if last_word is None:
            last_word = normalized
            used_words.add(normalized)
            last_user_id = message.author.id
            await message.add_reaction("✅")
            return

        # kontrola posledního písmene
        if normalized[0] == last_word[-1]:
            last_word = normalized
            used_words.add(normalized)
            last_user_id = message.author.id
            await message.add_reaction("✅")
        else:
            await message.delete()
            return

    # ===== POČÍTÁNÍ =====
    counting_channel_id = config.get("counting_channel_id")
    last_number = config.get("last_number")

    if counting_channel_id is not None and message.channel.id == counting_channel_id:
        content = message.content.strip()
        if not content.isdigit():
            await message.delete()
            return

        number = int(content)
        if last_number is None or number == last_number + 1:
            config["last_number"] = number
            save_config(config)
            await message.add_reaction("✅")
        else:
            await message.delete()

    rating_channel_id = config.get("rating_channel_id")

    if rating_channel_id and message.channel.id == rating_channel_id:
        lines = message.content.strip().splitlines()
        if len(lines) >= 2:
            match = re.match(r"([+-]\d+)b", lines[0].replace(" ", ""))
            if match and message.mentions:
                points = int(match.group(1))
                target = message.mentions[0]


                user_id = str(target.id)
                config["points"][user_id] = config["points"].get(user_id, 0) + points
                save_config(config)
                await message.add_reaction("✅")

    
    await bot.process_commands(message)





@bot.tree.command(
    name="set-hodnoceni",
    description="Nastaví aktuální kanál pro hodnocení bodů"
)
@app_commands.checks.has_permissions(administrator=True)
async def set_hodnoceni(interaction: discord.Interaction):
    config["rating_channel_id"] = interaction.channel.id
    save_config(config)
    await interaction.response.send_message(
        f"✅ Hodnocení bodů nastaveno v kanálu {interaction.channel.mention}",
        ephemeral=True
    )


@bot.tree.command(
    name="body",
    description="Vypíše bodové hodnocení"
)
async def body(interaction: discord.Interaction):
    points = config.get("points", {})

    if not points:
        await interaction.response.send_message("📭 Zatím nejsou žádné body.")
        return

    sorted_points = sorted(points.items(), key=lambda x: x[1], reverse=True)

    lines = []
    for user_id, score in sorted_points:
        try:
            user = await bot.fetch_user(int(user_id))
            name = user.name
        except:
            name = f"Uživatel {user_id}"

        lines.append(f"**{name}**: {score} bodů")

    await interaction.response.send_message(
        "🏆 **Bodové hodnocení:**\n" + "\n".join(lines)
    )


async def main():
    print("⏳ Čekám 5 sekund před přihlášením bota…")
    await asyncio.sleep(5)  # prodleva před loginem
    await bot.start(TOKEN)

if __name__ == "__main__":
    # spustíme hlavní async funkci bezpečně

    asyncio.run(main())



