# Bugs found

## 1. Reload flashing is too dim + no nav-away confirmation modal

Reload LiteLLM button flashes when a reload is needed. It should be flashier. Also, when trying to navigate away from the Models screen, it should popup with a reload or dismiss screen.

## 2. API Key popup is top-left aligned and ignores dark/light mode

Should be centered and respect the dark mode or light mode

## 3. Provider dropdown should not have a `None`/manual config option — Bedrock should be the default

Providers are necessary. Default is bedrock since it should always exist.

## 4. Poll Models button on Bedrock Add New Model gives a pagination error; region should be configurable in Providers

"Error: Failed to connect to Bedrock in region us-east-1: Operation cannot be paginated: list_foundation_models. Check network and region name." Additionally, the region dropdowns should be configurable in Providers

## 5. Bedrock Mantle models don't get a provider tag
