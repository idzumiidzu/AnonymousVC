import discord
from discord.ext import commands, tasks
from discord.ui import Button, View, Modal, TextInput, Select
from discord.utils import get
from discord import app_commands
import random
import os
import sqlite3

TOKEN = os.getenv('DISCORD_TOKEN')
DB_PATH = os.getenv('DATABASE_URL', '/app/data/tickets.db')

intents = discord.Intents.default()
intents.members = True  # メンバー関連のイベントを監視
intents.guilds = True   # ギルドの情報を監視
intents.message_content = True  # メッセージコンテンツ関連のイベント

bot = commands.Bot(command_prefix="/", intents=intents)

# データベース接続
def db_connect():
    return sqlite3.connect(DB_PATH)

# データベースの初期化
def initialize_db():
    with db_connect() as conn:
        cursor = conn.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS tickets (
            guild_id INTEGER,
            user_id INTEGER,
            tickets INTEGER DEFAULT 0,
            PRIMARY KEY (guild_id, user_id)
        )""")
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS invitations (
            guild_id INTEGER,
            user_id INTEGER,
            invites INTEGER DEFAULT 0,
            PRIMARY KEY (guild_id, user_id)
        )""")
        conn.commit()


initialize_db()

# チケットの取得
def get_tickets(guild_id, user_id):
    with db_connect() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT tickets FROM tickets WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
        result = cursor.fetchone()
        return result[0] if result else 0

# チケットの更新
def set_tickets(guild_id, user_id, tickets):
    with db_connect() as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO tickets (guild_id, user_id, tickets) VALUES (?, ?, ?)", (guild_id, user_id, tickets))
        conn.commit()

# 招待人数の取得
def get_invitations(guild_id, user_id):
    with db_connect() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT invites FROM invitations WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
        result = cursor.fetchone()
        return result[0] if result else 0

# 招待人数の更新
def set_invitations(guild_id, user_id, invites):
    with db_connect() as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO invitations (guild_id, user_id, invites) VALUES (?, ?, ?)", (guild_id, user_id, invites))
        conn.commit()


# プライベートVCのパスコード生成（重複を避ける）
def generate_passcode():
    while True:
        passcode = str(random.randint(1000, 9999))
        if passcode not in active_vcs:
            return passcode


# サーバーごとのグローバル変数
active_vcs = {}
monitor_vc_category = {}

# イベント: ボットがオンラインになったとき
@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        print(f"スラッシュコマンドが同期されました：{len(synced)}個のコマンド")
    except Exception as e:
        print(f"スラッシュコマンドの同期中にエラー: {e}")
    print(f"ログインしました: {bot.user}")
    update_private_vc_name.start()  # タスクを開始

@tasks.loop(minutes=2)
async def update_private_vc_name():
    for guild in bot.guilds:
        private_vc_channel_id = monitor_vc_category.get(guild.id)

        # プライベートVCのカテゴリが存在するか確認
        if private_vc_channel_id:
            private_vc_channel = discord.utils.get(guild.voice_channels, id=private_vc_channel_id)
            if private_vc_channel:
                # VCカテゴリ内で名前が"VC-"で始まるチャンネル数をカウント
                private_vc_count = len([vc for vc in private_vc_channel.category.voice_channels if vc.name.startswith("VC-")])
                new_name = f"非公開VCカウント: {private_vc_count}"

                # 名前を変更（変更が必要な場合のみ）
                if private_vc_channel.name != new_name:
                    try:
                        await private_vc_channel.edit(name=new_name)
                        print(f"{guild.name} のプライベートVC名を更新しました: {new_name}")
                    except discord.errors.HTTPException:
                        print("VC名の変更時にエラーが発生しましたが、スキップします。")


# サーバーごとにチケットや招待人数を管理するための関数
@bot.event
async def on_member_join(member):
    try:
        guild_id = member.guild.id
        invites = await member.guild.invites()
        for invite in invites:
            if invite.uses > 0:
                inviter = invite.inviter
                current_invites = get_invitations(guild_id, inviter.id)
                set_invitations(guild_id, inviter.id, current_invites + 1)

                current_tickets = get_tickets(guild_id, inviter.id)
                set_tickets(guild_id, inviter.id, current_tickets + 2)

                current_tickets_member = get_tickets(guild_id, member.id)
                set_tickets(guild_id, member.id, current_tickets_member + 1)
                break
    except Exception as e:
        print(f"on_member_joinでエラー: {e}")


class PasscodeModal(Modal):
    def __init__(self):
        super().__init__(title="パスコード入力")

        # パスコード入力フィールドを追加
        self.passcode = TextInput(
            label="パスコードを入力してください",
            placeholder="例: 1234",
            required=True,
            max_length=4
        )
        self.add_item(self.passcode)

    async def on_submit(self, interaction: discord.Interaction):
        passcode = self.passcode.value
        guild = interaction.guild

        # 入力されたパスコードを処理
        if guild.id in active_vcs and passcode in active_vcs[guild.id]:
            vc_info = active_vcs[guild.id][passcode]
            vc = vc_info["vc"]

            # アクセス権を付与
            await vc.set_permissions(interaction.user, view_channel=True, connect=True)
            vc_info["participants"].append(interaction.user)

            await interaction.response.send_message(
                f"{vc.mention} にアクセス権が付与されました！", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "無効なパスコードです。再確認してください。", ephemeral=True
            )


class PrivateVCPanel(View):
    def __init__(self, category):
        super().__init__(timeout=None)
        self.category = category

        self.create_vc_button = Button(label="プライベートVCを作成", style=discord.ButtonStyle.green)
        self.access_vc_button = Button(label="パスコードを入力して参加", style=discord.ButtonStyle.green)
        self.check_tickets_button = Button(label="チケット数を確認", style=discord.ButtonStyle.blurple)

        self.create_vc_button.callback = self.create_vc_callback
        self.access_vc_button.callback = self.access_vc_callback
        self.check_tickets_button.callback = self.check_tickets_callback

        self.add_item(self.create_vc_button)
        self.add_item(self.access_vc_button)
        self.add_item(self.check_tickets_button)

    async def create_vc_callback(self, interaction: discord.Interaction):
        user = interaction.user
        guild_id = interaction.guild.id
        guild = interaction.guild
        tickets = get_tickets(guild_id,user.id)

        if tickets > 0:
            set_tickets(guild_id,user.id, tickets - 1)
            passcode = generate_passcode()
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False, connect=False),
                user: discord.PermissionOverwrite(view_channel=True, connect=True)
            }

            vc = await self.category.create_voice_channel(
                name=f"VC-{passcode}",
                overwrites=overwrites,
                user_limit=2
            )

            if guild.id not in active_vcs:
                active_vcs[guild.id] = {}

            active_vcs[guild.id][passcode] = {
                "vc": vc,
                "creator": user,
                "participants": [user]
            }

            await interaction.response.send_message(
                f"プライベートVCが作成されました！\nパスコード: `{passcode}`\n{vc.mention} に参加できます。\n残りチケット数: {get_tickets(guild_id,user.id)}枚",
                ephemeral=True
            )
        else:
            await interaction.response.send_message("チケットが足りません！", ephemeral=True)

    async def access_vc_callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(PasscodeModal())

    async def check_tickets_callback(self, interaction: discord.Interaction):
        user = interaction.user
        guild_id = interaction.guild.id
        await interaction.response.send_message(
            f"あなたの現在のチケット数: {get_tickets(guild_id,user.id)}枚", ephemeral=True
        )


class PaginatedSelectView(View):
    def __init__(self, channels, author):
        super().__init__()
        self.channels = channels
        self.author = author  # インタラクションを行ったユーザーを設定

        # チャンネルを選択するプルダウンメニュー
        self.channel_select = Select(
            placeholder="チャンネルを選んでください",
            options=[discord.SelectOption(label=channel.name, value=str(channel.id)) for channel in channels]
        )
        self.channel_select.callback = self.on_channel_selected  # callbackメソッドの設定
        self.add_item(self.channel_select)

    async def interaction_check(self, interaction: discord.Interaction):
        if interaction.user != self.author:
            await interaction.response.send_message("他のユーザーは操作できません。", ephemeral=True)
            return False
        return True

    async def on_channel_selected(self, interaction):
        # チャンネルが選択された際にパネルを設置
        selected_channel = discord.utils.get(self.channels, id=int(self.channel_select.values[0]))
        if selected_channel:
            panel_message = await selected_channel.send(
                content="### 以下のボタンを使用してください",
                view=PrivateVCPanel(selected_channel.category)  # ここでVCパネルを設置する
            )

            # ユーザーにパネルが設置されたことを通知
            await interaction.response.send_message(
                f"チャンネル「{selected_channel.name}」にパネルを設置しました！",
                ephemeral=True
            )
        else:
            await interaction.response.send_message("チャンネルの選択に失敗しました。", ephemeral=True)

    async def update_channels(self):
        # チャンネルを選んでくださいメッセージを送信
        await self.message.edit(
            content="チャンネルを選んでください。",
            view=self
        )

@bot.tree.command(name="reset_all_tickets", description="このサーバーの全メンバーのチケットをリセットします（管理者限定）")
@app_commands.default_permissions(administrator=True)  # 管理者のみ実行可能
async def reset_all_tickets(interaction: discord.Interaction):
    guild_id = interaction.guild.id  # コマンドが実行されたサーバーのIDを取得

    with db_connect() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE tickets SET tickets = 0 WHERE guild_id = ?", (guild_id,))
        conn.commit()

    await interaction.response.send_message("このサーバーの全メンバーのチケットをリセットしました。", ephemeral=True)  # 管理者のみが見えるように

@bot.tree.command(name="setup", description="プライベートVC作成パネルを設定します。")
async def setup(interaction: discord.Interaction):
    guild = interaction.guild
    categories = guild.categories
    if not categories:
        await interaction.response.send_message("カテゴリが見つかりません。", ephemeral=True)
        return

    category_options = [discord.SelectOption(label=category.name, value=str(category.id)) for category in categories[:25]]

    class CategorySelect(discord.ui.Select):
        def __init__(self):
            super().__init__(placeholder="VCが作成されるカテゴリを選んでください", options=category_options)

        async def callback(self, interaction: discord.Interaction):
            guild = interaction.guild
            selected_category = discord.utils.get(guild.categories, id=int(self.values[0]))

            # カテゴリ内のチャンネルをリスト化
            channels = selected_category.text_channels
            if not channels:
                await interaction.response.send_message("このカテゴリにチャンネルが見つかりません。", ephemeral=True)
                return

            # PaginatedSelectViewのインスタンス化時に、authorを渡す
            channel_view = PaginatedSelectView(channels, author=interaction.user)
            await interaction.response.send_message(
                content="パネルを設置するチャンネルを選んでください",
                view=channel_view,
                ephemeral=True  # 自分にしか見えないメッセージ
            )


    category_view = View()
    category_view.add_item(CategorySelect())

    await interaction.response.send_message(
        content="VCが作成されるカテゴリを選んでください",
        view=category_view,
        ephemeral=True  # 自分にしか見えないメッセージ
    )



# VC参加処理の修正
@bot.tree.command(name="vc", description="プライベートVCに参加します。")
async def vc(interaction: discord.Interaction, passcode: str):
    guild = interaction.guild
    if guild.id in active_vcs and passcode in active_vcs[guild.id]:
        vc_info = active_vcs[guild.id][passcode]
        vc = vc_info["vc"]
        await vc.set_permissions(interaction.user, view_channel=True, connect=True)
        vc_info["participants"].append(interaction.user)
        await interaction.response.send_message(f"{vc.mention} にアクセス権が付与されました！", ephemeral=True)
    else:
        await interaction.response.send_message("無効なパスコードです。", ephemeral=True)



# VCの参加者が変更されたときの処理
@bot.event
async def on_voice_state_update(member, before, after):
    guild = member.guild
    if guild.id in active_vcs:
        # VCから退出して参加者がいなくなった場合
        if before.channel and len(before.channel.members) == 0:
            for passcode, vc_data in list(active_vcs[guild.id].items()):  # active_vcsをリスト化して安全に削除
                if vc_data["vc"].id == before.channel.id:  # 該当VCがプライベートVCの場合
                    await before.channel.delete()  # VCを削除
                    del active_vcs[guild.id][passcode]  # active_vcsから削除
                    break  # 一度見つかったらループ終了

        # VCに新たに参加した場合
        if after.channel and before.channel != after.channel:
            for passcode, vc_data in active_vcs[guild.id].items():
                if vc_data["vc"].id == after.channel.id:  # プライベートVCに参加した場合
                    vc_data["participants"].append(member)
                    break

# 監視用のカテゴリ設定

# カスタムセレクトメニュークラス
class CategorySelect(discord.ui.Select):
    def __init__(self, categories):
        options = [
            discord.SelectOption(label=category.name, value=str(category.id))
            for category in categories[:25]  # 25個までに制限
        ]
        super().__init__(placeholder="監視用のカテゴリを選択してください", options=options)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()  # インタラクションの遅延処理

        guild = interaction.guild
        selected_category_id = int(self.values[0])
        category = discord.utils.get(guild.categories, id=selected_category_id)

        # 「非公開VCカウント： 数」のチャンネルを作成
        private_vc_channel = next(filter(lambda vc: "非公開VCカウント：" in vc.name, category.channels), None)
        if not private_vc_channel:
            private_vc_channel = await category.create_voice_channel(
                name="非公開VCカウント： 0",
                overwrites={guild.default_role: discord.PermissionOverwrite(connect=False)},
            )

        # チャンネルIDを保存（サーバーごとに保存するなら辞書などを利用）
        monitor_vc_category[guild.id] = private_vc_channel.id

        # 作成された「非公開VCカウント： 数」の名前を更新
        private_vc_count = len([vc for vc in category.voice_channels if vc.name.startswith("VC-")])
        await private_vc_channel.edit(name=f"非公開VCカウント： {private_vc_count}")

        await interaction.followup.send(
            f"監視用のカテゴリを「{category.name}」に設定しました。", ephemeral=True
        )

# カスタムViewクラス
class CategorySelectView(discord.ui.View):
    def __init__(self, categories):
        super().__init__()
        self.add_item(CategorySelect(categories))

# コマンド本体
@bot.tree.command(name="setup_monitor", description="プライベートVC監視カテゴリを設定します。")
async def setup_monitor(interaction: discord.Interaction):
    guild = interaction.guild
    categories = guild.categories

    if not categories:
        await interaction.response.send_message("エラー: カテゴリが見つかりません", ephemeral=True)
        return

    # Viewを作成し、送信
    view = CategorySelectView(categories)
    await interaction.response.send_message("監視用のカテゴリを選択してください。", view=view, ephemeral=True)


# 1. メンバー全員に1チケット付与
@bot.tree.command(name="give_all_tickets", description="サーバーの全員に1チケットを付与します。")
@commands.has_permissions(administrator=True)
async def give_all_tickets(interaction: discord.Interaction):
    try:
        guild_id = interaction.guild.id
        for member in interaction.guild.members:
            current_tickets = get_tickets(guild_id,member.id)
            set_tickets(guild_id, member.id, current_tickets + 1)

        await interaction.response.send_message("全員に1チケットを付与しました。", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"エラーが発生しました: {e}", ephemeral=True)


# 2. 自分のチケットと招待人数確認
@bot.tree.command(name="my_info", description="自分のチケットと招待人数を確認します。")
async def my_info(interaction: discord.Interaction):
    user = interaction.user
    guild_id = interaction.guild.id
    tickets = get_tickets(guild_id, user.id)
    invites = get_invitations(guild_id, user.id)
    await interaction.response.send_message(f"あなたのチケット数: {tickets}枚\n招待人数: {invites}人", ephemeral=True)


# 3. メンバーを指定してチケットと招待人数確認
@bot.tree.command(name="check_member_info", description="指定したメンバーのチケットと招待人数を確認します。")
async def check_member_info(interaction: discord.Interaction, member: discord.Member):
    guild_id = interaction.guild.id
    tickets = get_tickets(guild_id, member.id)
    invites = get_invitations(guild_id, member.id)
    await interaction.response.send_message(f"{member.mention}のチケット数: {tickets}枚\n招待人数: {invites}人", ephemeral=True)


# 4. メンバーを指定してチケットの数変更
@bot.tree.command(name="set_member_tickets", description="指定したメンバーのチケット数を変更します。")
@commands.has_permissions(administrator=True)
async def set_member_tickets(interaction: discord.Interaction, member: discord.Member, tickets: int):
    try:
        guild_id = interaction.guild.id
        set_tickets(guild_id, member.id, tickets)
        await interaction.response.send_message(f"{member.mention}のチケット数を{tickets}枚に設定しました。", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"エラーが発生しました: {e}", ephemeral=True)


bot.run(TOKEN)
