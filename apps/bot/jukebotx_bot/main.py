# apps/bot/jukebotx_bot/main.py
import discord

from jukebotx_bot.settings import load_bot_settings


def main() -> None:
    settings = load_bot_settings()

    intents = discord.Intents.default()
    intents.message_content = True

    client = discord.Client(intents=intents)

    @client.event
    async def on_ready() -> None:
        """
        Discord.py lifecycle hook fired when the client has successfully connected
        and the bot user identity is available.

        This is the earliest safe place to validate that the *actual Discord identity*
        matches the expected environment (dev vs prod). These checks prevent
        accidentally running a production deployment with the dev bot token, or vice versa.
        """
        # Defensive: discord.py guarantees `client.user` in on_ready, but keep this explicit
        # so failures are obvious if lifecycle behavior changes.
        assert client.user is not None, "client.user is unexpectedly None in on_ready()"

        bot_name = client.user.name.lower().strip()
        env = settings.env.lower().strip()

        # 1) Never allow a dev-named bot identity to run in production.
        assert (
            env != "production" or "dev" not in bot_name
        ), (
            "Safety check failed: ENV=production but the connected Discord bot name "
            "contains 'dev'. You are likely using the DEV bot token in production."
        )

        # 2) In development, require a dev-named bot identity to reduce the chance
        # of accidentally using the production bot token while testing.
        assert (
            env != "development" or "dev" in bot_name
        ), (
            "Safety check failed: ENV=development but the connected Discord bot name "
            "does NOT contain 'dev'. You are likely using the production bot token in development."
        )

        print(f"Connected as {client.user} (env={settings.env})")

    client.run(settings.active_discord_token)


if __name__ == "__main__":
    main()
