from typing import TYPE_CHECKING
import random

import discord
from discord import app_commands
from discord.ext import commands

from achievements.models import Achievement as AchievementModel, PlayerAchievement
from ballsdex.core.bot import BallsDexBot
from ballsdex.core.utils.menus.formatter import ItemFormatter
from ballsdex.core.utils.menus.menus import Menu
from ballsdex.core.utils.menus.source import ChunkedListSource
from ballsdex.settings import settings
from bd_models.models import BallInstance, Player


from .transformers import AchievementEnabledTransform

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot


class Achievement(commands.GroupCog):
    """Achievement commands."""

    def __init__(self, bot: "BallsDexBot"):
        self.bot = bot

    @app_commands.command()
    async def list(self, interaction: discord.Interaction):
        """List all available achievements."""
        await interaction.response.defer(ephemeral=True)

        achievements = [
            achievement async for achievement in AchievementModel.objects.filter(enable=True)
        ]
        player, _ = await Player.objects.aget_or_create(discord_id=interaction.user.id)
        claimed_achievement_ids = {
            player_achievement.achievement_id
            async for player_achievement in PlayerAchievement.objects.filter(player=player)
        }

        if not achievements:
            await interaction.followup.send(
                "There are no achievements currently registered in the admin panel.",
                ephemeral=True,
            )
            return

        entries = []
        for achievement in achievements:
            emote = self.bot.get_emoji(achievement.achievement_emoji_id) or ""
            status = "✅" if achievement.id in claimed_achievement_ids else "❌"
            entry_lines = [f"Requirements: {achievement.description} {status}"]

            rewards = [ball async for ball in achievement.reward.all()]
            if rewards:
                entry_lines.append(
                    f"Rewards: {', '.join(ball.country for ball in rewards)}"
                )

            entry_text = "\n".join(entry_lines)
            entries.append(discord.ui.TextDisplay(f"## {emote} {achievement.name}\n{entry_text}"))

        view = discord.ui.LayoutView()
        container = discord.ui.Container()
        container.add_item(
            discord.ui.TextDisplay(f"# {settings.bot_name} Achievements list")
        )
        container.add_item(discord.ui.Separator())
        view.add_item(container)

        menu = Menu(
            self.bot,
            view,
            ChunkedListSource(entries, per_page=10),
            ItemFormatter(container, position=2),
        )
        await menu.init()
        await interaction.followup.send(view=view, ephemeral=True)

    @app_commands.command()
    async def claim(
        self,
        interaction: discord.Interaction,
        achievement: AchievementEnabledTransform,
    ):
        """Claim an achievement when its requirements are met."""
        await interaction.response.defer(ephemeral=True)
        player, _ = await Player.objects.aget_or_create(discord_id=interaction.user.id)

        if await PlayerAchievement.objects.filter(
            player=player, achievement=achievement
        ).aexists():
            await interaction.followup.send(
                f"You already claimed the achievement **{achievement.name}**!",
                ephemeral=True,
            )
            return

        required_ball_ids = {
            ball.id async for ball in achievement.required_balls.all()
        }
        required_specials = [
            special async for special in achievement.special_required.all()
        ]
        required_special_ids = {special.id for special in required_specials}

        player_instances_query = BallInstance.objects.filter(player=player).select_related(
            "ball", "special"
        )
        if achievement.self_catch:
            player_instances_query = player_instances_query.filter(
                trade_player_id__isnull=True
            )
        player_instances = [
            ball_instance async for ball_instance in player_instances_query
        ]

        if not required_ball_ids and required_special_ids:
            special_count = sum(
                ball_instance.special_id in required_special_ids
                for ball_instance in player_instances
            )
            if special_count < achievement.required_quantity:
                special_names = ", ".join(str(special) for special in required_specials)
                note = " (must be self-caught)" if achievement.self_catch else ""
                await interaction.followup.send(
                    f"You need {achievement.required_quantity} of these specials{note}: "
                    f"{special_names}. You have {special_count}.",
                    ephemeral=True,
                )
                return

        elif required_ball_ids:
            if not required_special_ids:
                owned_ball_ids = {ball_instance.ball_id for ball_instance in player_instances}
                missing_balls = [
                    ball
                    async for ball in achievement.required_balls.all()
                    if ball.id not in owned_ball_ids
                ]
                if missing_balls:
                    note = " (must be self-caught)" if achievement.self_catch else ""
                    countries = ", ".join(ball.country for ball in missing_balls)
                    await interaction.followup.send(
                        f"Missing required countryballs{note}: {countries}",
                        ephemeral=True,
                    )
                    return
            else:
                missing_ball_ids = {
                    ball_id
                    for ball_id in required_ball_ids
                    if not any(
                        ball_instance.ball_id == ball_id
                        and ball_instance.special_id in required_special_ids
                        for ball_instance in player_instances
                    )
                }
                if missing_ball_ids:
                    missing_balls = [
                        ball
                        async for ball in achievement.required_balls.all()
                        if ball.id in missing_ball_ids
                    ]
                    special_names = ", ".join(
                        str(special) for special in required_specials
                    )
                    note = " (must be self-caught)" if achievement.self_catch else ""
                    await interaction.followup.send(
                        f"Missing required countryballs{note}: "
                        f"{', '.join(ball.country for ball in missing_balls)} "
                        f"with any of [{special_names}]",
                        ephemeral=True,
                    )
                    return

        try:
            reward_ball_ids = [ball.id async for ball in achievement.reward.all()]
            for reward_ball_id in reward_ball_ids:
                await BallInstance.objects.acreate(
                    player=player,
                    ball_id=reward_ball_id,
                    special=None,
                    health_bonus=random.randint(
                        -settings.max_health_bonus, settings.max_health_bonus
                    ),
                    attack_bonus=random.randint(
                        -settings.max_attack_bonus, settings.max_attack_bonus
                    ),
                )
        except Exception:
            await interaction.followup.send(
                "The achievement could not be claimed because its reward could not be created.",
                ephemeral=True,
            )
            return

        await PlayerAchievement.objects.acreate(player=player, achievement=achievement)
        await interaction.followup.send(
            f"🎉 Congratulations, you claimed **{achievement.name}**!",
            ephemeral=True,
        )
