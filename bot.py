import datetime
import os
from threading import Thread
import discord
from discord.ext import commands, tasks
from discord.ui import Select, View, button, Button
from flask import Flask
from dotenv import load_dotenv

load_dotenv()  # ตัวสั่งให้ Python อ่านไฟล์ .env ในเครื่อง

# ---------------------------------------------------------
# Web Server สำหรับ Keep Alive / Health Check
# ---------------------------------------------------------
app = Flask('')

@app.route('/')
def home():
    return "Bot is running 24/7!"

def run_web():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_web)
    t.daemon = True
    t.start()

# ---------------------------------------------------------
# การตั้งค่า Bot และ Intents
# ---------------------------------------------------------
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ---------------------------------------------------------
# ระบบเก็บข้อมูลในหน่วยความจำ (In-Memory Data Structures)
# ---------------------------------------------------------
booked_houses = {}      # Format: {"MAIN-1": user_id, "IN1-3": user_id}
user_time_slots = {}    # Format: {user_id: "21:00"}

HOUSES_CONFIG = {
    "MAIN": {"label": "👑 บ้านหลัก", "placeholder": "ปลดล็อกบ้านหลัก", "count": 10, "prefix": "บ้านหลัก"},
    "IN1": {"label": "🛡️ บ้านใน (1)", "placeholder": "ปลดล็อกบ้านใน (1)", "count": 8, "prefix": "บ้านใน 1"},
    "IN2": {"label": "🛡️ บ้านใน (2)", "placeholder": "ปลดล็อกบ้านใน (2)", "count": 8, "prefix": "บ้านใน 2"},
    "IN3": {"label": "🛡️ บ้านใน (3)", "placeholder": "ปลดล็อกบ้านใน (3)", "count": 8, "prefix": "บ้านใน 3"},
}

TIME_SLOTS = {
    "18:00": "🌆 หลัง 6 โมงเย็น",
    "21:00": "🌃 หลัง 3 ทุ่ม",
    "22:00": "🌙 หลัง 4 ทุ่ม",
    "23:00": "🌌 หลัง 5 ทุ่ม",
}

# ตัวแปร Global สำหรับเก็บอ้างอิงข้อความ (Message References)
user_war_message = None   
user_time_message = None  
admin_house_message = None  
admin_time_message = None   


# ---------------------------------------------------------
# 1. User UI Components (สำหรับห้องจองของลูกกิลด์)
# ---------------------------------------------------------
class HouseSelect(Select):
    def __init__(self, zone_key: str):
        self.zone_key = zone_key
        config = HOUSES_CONFIG[zone_key]
        
        options = []
        for i in range(1, config["count"] + 1):
            house_id = f"{zone_key}-{i}"
            
            if house_id in booked_houses:
                options.append(discord.SelectOption(
                    label=f"{config['prefix']} - {i} (จองแล้ว)",
                    value=house_id,
                    description="กดซ้ำเพื่อยกเลิกการจอง",
                    emoji="🔴"
                ))
            else:
                options.append(discord.SelectOption(
                    label=f"{config['prefix']} - {i} (ว่าง)",
                    value=house_id,
                    description="กดเพื่อจองบ้านหลังนี้",
                    emoji="🟢"
                ))

        super().__init__(
            placeholder=f"🔽 {config['placeholder'].replace('ปลดล็อก', 'จอง')}",
            min_values=1,
            max_values=1,
            options=options,
            custom_id=f"select_user_{zone_key}"
        )

    async def callback(self, interaction: discord.Interaction):
        selected_house_id = self.values[0]
        user = interaction.user

        if selected_house_id in booked_houses:
            if booked_houses[selected_house_id] == user.id:
                del booked_houses[selected_house_id]
        else:
            booked_houses[selected_house_id] = user.id

        await interaction.response.defer()
        await refresh_all_views()


class WarDashboardView(View):
    def __init__(self):
        super().__init__(timeout=None)
        for zone in ["MAIN", "IN1", "IN2", "IN3"]:
            self.add_item(HouseSelect(zone))


# ---------------------------------------------------------
# 2. Time Selection UI Components (สำหรับห้องเลือกเวลาตี)
# ---------------------------------------------------------
class TimeSelect(Select):
    def __init__(self):
        options = []
        for time_key, label in TIME_SLOTS.items():
            options.append(discord.SelectOption(
                label=label,
                value=time_key,
                description="กดเพื่อเลือก/เปลี่ยนรอบเวลาที่สะดวก"
            ))

        super().__init__(
            placeholder="⏱️ เลือกช่วงเวลาที่คุณสะดวกตี...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="select_time_slot"
        )

    async def callback(self, interaction: discord.Interaction):
        selected_time = self.values[0]
        user_id = interaction.user.id

        if user_time_slots.get(user_id) == selected_time:
            del user_time_slots[user_id]
        else:
            user_time_slots[user_id] = selected_time

        await interaction.response.defer()
        await refresh_all_views()


class TimeDashboardView(View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TimeSelect())


# ---------------------------------------------------------
# 3. Admin UI Components (แผงควบคุมของแอดมิน)
# ---------------------------------------------------------
class AdminUnbookSelect(Select):
    def __init__(self, zone_key: str):
        self.zone_key = zone_key
        config = HOUSES_CONFIG[zone_key]
        
        options = []
        for i in range(1, config["count"] + 1):
            house_id = f"{zone_key}-{i}"
            if house_id in booked_houses:
                user_id = booked_houses[house_id]
                options.append(discord.SelectOption(
                    label=f"ปลดล็อก: {config['prefix']} - {i}",
                    value=house_id,
                    description=f"ผู้จอง ID: {user_id}",
                    emoji="🔓"
                ))

        if not options:
            options.append(discord.SelectOption(
                label=f"{config['prefix']}: ไม่มีคนจอง",
                value="none"
            ))

        super().__init__(
            placeholder=f"🏠 {config['placeholder']}",
            min_values=1,
            max_values=1,
            options=options,
            disabled=(options[0].value == "none"),
            custom_id=f"select_admin_{zone_key}"
        )

    async def callback(self, interaction: discord.Interaction):
        selected_house_id = self.values[0]
        await interaction.response.defer()

        if selected_house_id in booked_houses:
            del booked_houses[selected_house_id]

        await refresh_all_views()


class AdminRemoveTimeSelect(Select):
    def __init__(self, time_key: str):
        self.time_key = time_key
        label_title = TIME_SLOTS[time_key]
        
        users_in_this_slot = [uid for uid, slot in user_time_slots.items() if slot == time_key]
        
        options = []
        for uid in users_in_this_slot:
            options.append(discord.SelectOption(
                label=f"ปลดเวลา ID: {uid}",
                value=str(uid),
                description=f"ลบออกจากรอบ {label_title}",
                emoji="❌"
            ))

        if not options:
            options.append(discord.SelectOption(
                label=f"{label_title}: ไม่มีคนลงเวลา",
                value="none"
            ))

        super().__init__(
            placeholder=f"⏱️ ลบสมาชิกจาก: {label_title}",
            min_values=1,
            max_values=1,
            options=options,
            disabled=(options[0].value == "none"),
            custom_id=f"select_admin_remove_time_{time_key}"
        )

    async def callback(self, interaction: discord.Interaction):
        selected_user_id = int(self.values[0])
        await interaction.response.defer()

        if selected_user_id in user_time_slots:
            del user_time_slots[selected_user_id]

        await refresh_all_views()


class AdminHouseControlView(View):
    def __init__(self):
        super().__init__(timeout=None)
        for zone in ["MAIN", "IN1", "IN2", "IN3"]:
            self.add_item(AdminUnbookSelect(zone))

    @button(label="🧹 ล้างข้อมูลการจองบ้านทั้งหมด", style=discord.ButtonStyle.primary, row=4)
    async def reset_houses_button(self, interaction: discord.Interaction, button: Button):
        booked_houses.clear()
        # สั่งอัปเดตหน้าต่าง UI ตัวเองทันทีเพื่อไม่ให้ค้าง
        await interaction.response.edit_message(view=AdminHouseControlView())
        # สั่งรีเฟรชข้อความห้องอื่นๆ ทั้งหมด
        await refresh_all_views()


class AdminTimeControlView(View):
    def __init__(self):
        super().__init__(timeout=None)
        for time_key in TIME_SLOTS.keys():
            self.add_item(AdminRemoveTimeSelect(time_key))

    @button(label="⚠️ ล้างข้อมูลทั้งหมด (บ้าน + เวลา)", style=discord.ButtonStyle.danger, row=4)
    async def reset_all_button(self, interaction: discord.Interaction, button: Button):
        booked_houses.clear()
        user_time_slots.clear()
        # สั่งอัปเดตหน้าต่าง UI ตัวเองทันทีเพื่อไม่ให้ค้าง
        await interaction.response.edit_message(view=AdminTimeControlView())
        # สั่งรีเฟรชข้อความห้องอื่นๆ ทั้งหมด
        await refresh_all_views()


# ---------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------
def create_war_embed():
    embed = discord.Embed(
        title="⚔️ **ตารางสรุปสถานะการจองบ้านกิลด์วอร์**",
        description=(
            "🍃 **บ้านนอก:** สามารถเข้าตีได้ทันทีโดยไม่ต้องกดจอง\n"
            "─────────────────────────────────────────"
        ),
        color=discord.Color.blue()
    )

    main_status = []
    for i in range(1, 11):
        house_id = f"MAIN-{i}"
        if house_id in booked_houses:
            user_id = booked_houses[house_id]
            main_status.append(f"🔴 **{i}**: <@{user_id}>")
        else:
            main_status.append(f"⚪ `{i}`")

    embed.add_field(
        name=f"{HOUSES_CONFIG['MAIN']['label']}",
        value=" | ".join(main_status) + "\n─────────────────────────────────────────",
        inline=False
    )

    for zone_key in ["IN1", "IN2", "IN3"]:
        config = HOUSES_CONFIG[zone_key]
        status_list = []
        for i in range(1, config["count"] + 1):
            house_id = f"{zone_key}-{i}"
            if house_id in booked_houses:
                user_id = booked_houses[house_id]
                status_list.append(f"🔴 **{i}**: <@{user_id}>")
            else:
                status_list.append(f"⚪ `{i}`")
        
        column_content = "\n".join(status_list) + "\n─────────────"
        
        embed.add_field(
            name=f"{config['label']}",
            value=column_content,
            inline=True
        )

    embed.set_footer(text="ระบบอัปเดตสถานะอัตโนมัติแบบ Real-time")
    return embed


def create_time_embed():
    embed = discord.Embed(
        title="⏱️ **ตารางสรุปช่วงเวลาสะดวกตีของสมาชิก**",
        description=(
            "💡 **หมายเหตุ:** สมาชิกที่ไม่กดเลือกช่วงเวลา ถือว่า **สะดวกสแตนบายตีได้ทั้งวัน**\n"
            "*(หากต้องการยกเลิก หรือกลับไปสแตนบาย ให้กดเลือกช่วงเวลาเดิมซ้ำอีกครั้ง)*\n"
            "─────────────────────────────────────────"
        ),
        color=discord.Color.green()
    )

    for time_key, label in TIME_SLOTS.items():
        users_in_slot = [f"<@{uid}>" for uid, slot in user_time_slots.items() if slot == time_key]
        
        if users_in_slot:
            user_list_str = " • " + "\n • ".join(users_in_slot)
            value_str = f"{user_list_str}\n\n`รวม {len(users_in_slot)} คน`"
        else:
            value_str = "`— ยังไม่มีผู้เลือก —`"

        value_str += "\n─────────────────────────────────────────"

        embed.add_field(
            name=f"**{label}**",
            value=value_str,
            inline=False
        )

    embed.set_footer(text="ระบบอัปเดตสถานะอัตโนมัติแบบ Real-time")
    return embed


async def refresh_all_views():
    """อัปเดตข้อความและเมนูทั้งฝั่งลูกกิลด์และแอดมินทุกห้องให้ตรงกับข้อมูลปัจจุบัน"""
    global user_war_message, user_time_message, admin_house_message, admin_time_message

    # 1. อัปเดตห้องจองบ้านของลูกกิลด์
    if user_war_message:
        try:
            await user_war_message.edit(embed=create_war_embed(), view=WarDashboardView())
        except Exception as e:
            print(f"Error refreshing user war view: {e}")

    # 2. อัปเดตห้องลงเวลาของลูกกิลด์
    if user_time_message:
        try:
            await user_time_message.edit(embed=create_time_embed(), view=TimeDashboardView())
        except Exception as e:
            print(f"Error refreshing user time view: {e}")

    # 3. อัปเดตแผงปลดล็อกบ้านฝั่งแอดมิน
    if admin_house_message:
        try:
            await admin_house_message.edit(view=AdminHouseControlView())
        except Exception as e:
            print(f"Error refreshing admin house view: {e}")

    # 4. อัปเดตแผงลบเวลาฝั่งแอดมิน
    if admin_time_message:
        try:
            await admin_time_message.edit(view=AdminTimeControlView())
        except Exception as e:
            print(f"Error refreshing admin time view: {e}")


# ---------------------------------------------------------
# Automated Tasks & Bot Commands
# ---------------------------------------------------------
RESET_TIME = datetime.time(hour=7, minute=0, second=0)

@tasks.loop(time=RESET_TIME)
async def auto_reset_task():
    global booked_houses, user_time_slots
    booked_houses.clear()
    user_time_slots.clear()
    await refresh_all_views()

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user.name}")
    if not auto_reset_task.is_running():
        auto_reset_task.start()

@bot.command(name="setup_war")
@commands.has_permissions(administrator=True)
async def setup_war(ctx):
    global user_war_message
    try:
        await ctx.message.delete()
    except Exception as e:
        print(f"Delete message error: {e}")
    
    menu_view = WarDashboardView()
    embed = create_war_embed()
    user_war_message = await ctx.send("⚔️ **ระบบจองบ้านกิลด์วอร์**", embed=embed, view=menu_view)

@bot.command(name="setup_time")
@commands.has_permissions(administrator=True)
async def setup_time(ctx):
    global user_time_message
    try:
        await ctx.message.delete()
    except Exception as e:
        print(f"Delete message error: {e}")
    
    menu_view = TimeDashboardView()
    embed = create_time_embed()
    user_time_message = await ctx.send(embed=embed, view=menu_view)

@bot.command(name="setup_admin")
@commands.has_permissions(administrator=True)
async def setup_admin(ctx):
    global admin_house_message, admin_time_message
    try:
        await ctx.message.delete()
    except Exception as e:
        print(f"Delete message error: {e}")
    
    house_view = AdminHouseControlView()
    admin_house_message = await ctx.send("🏠 **[Admin Panel 1/2] แผงบังคับปลดล็อกบ้าน**", view=house_view)

    time_view = AdminTimeControlView()
    admin_time_message = await ctx.send("⏱️ **[Admin Panel 2/2] แผงบังคับลบเวลาตีของสมาชิก**", view=time_view)

@bot.command(name="reset_war")
@commands.has_permissions(administrator=True)
async def reset_war(ctx):
    global booked_houses, user_time_slots
    try:
        await ctx.message.delete()
    except Exception as e:
        print(f"Delete message error: {e}")
        
    booked_houses.clear()
    user_time_slots.clear()
    await refresh_all_views()

# ---------------------------------------------------------
# จุดเริ่มต้นการทำงาน
# ---------------------------------------------------------
if __name__ == "__main__":
    keep_alive()  # เรียกเปิด Web Server ตรวจสอบสถานะ
    
    TOKEN = os.environ.get("DISCORD_TOKEN")
    if TOKEN:
        bot.run(TOKEN)
    else:
        print("Error: ไม่พบ DISCORD_TOKEN ใน Environment Variable")
