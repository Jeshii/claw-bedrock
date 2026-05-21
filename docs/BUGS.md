# Bugs found

## 1. Reload flashing is too dim

Reload LiteLLM button flashes when a reload is needed. It should be flashier. Also, when trying to navigate away from the Models screen, it should popup with a reload or dismiss screen.

## 2. API Key popup window is aligned top left and white

Should be centered and respect the dark mode or light mode

## 3. Dark mode button is flat upon first load

The Dark Icon won't show up until the button is clicked.

Provider dropdown should not have None/manual config. That's not an option anymore. Providers are necessary. Default is bedrock since it should always exist.

## 4. Poll Models button on Bedrock Add New Model give an error

"Error: Failed to connect to Bedrock in region us-east-1: Operation cannot be paginated: list_foundation_models. Check network and region name." Addionally, the region dropdowns should be configurable in Providers

## 5. Providers tab should be above models tab

## 6. Backup tab should be after Tags

## 7. Log windows should be resizable

## 8. Bedrock Mantle models don't get a provider tag

## 9. Model added — reload LiteLLM to apply toast never dissappears