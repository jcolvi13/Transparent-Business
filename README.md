Phase0.py : Python script that sends a single "Hello!" request to OpenRouter and prints the model response.

Phase1.py : Creates simple terminal chat loop, each message handled without memory

Phase2.py : Creates running message history, so that the chat can be continuous in the terminal

Phase3.py : Introduces a basic agent loop where the model can reply with COMMAND: or DONE: and shell commands are executed automatically without extra explanation.

Phase4.py : cleans up agent loop and make it more reliable

Phase5.py : Adds STOCKAGENT.md and COMPARESKILLS.md (will later add ETHICSSKILLS.md) to specifically have the agent grab stock data (and ethical data) from specified sources and output a short summary comparing stocks (For Phase 2 MVP progress we are just testing the STOCKSKILLS accuracy and reporting of up to date stock data to the user)
