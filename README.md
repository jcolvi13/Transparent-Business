Phase0.py : Python script that sends a single "Hello!" request to OpenRouter and prints the model response.

Phase1.py : Creates simple terminal chat loop, each message handled without memory

Phase2.py : Creates running message history, so that the chat can be continuous in the terminal

Phase3.py : Introduces a basic agent loop where the model can reply with COMMAND: or DONE: and shell commands are executed automatically without extra explanation.

Phase4.py : cleans up agent loop and make it more reliable

Phase5.py : Adds STOCKAGENT.md and COMPARESKILLS.md (will later add ETHICSSKILLS.md) to specifically have the agent grab stock data (and ethical data) from specified sources and output a short summary comparing stocks (For Phase 2 MVP progress we are just testing the STOCKSKILLS accuracy and reporting of up to date stock data to the user)

Phase6.py creates local web app for cleaner interface outside of terminal


DEMO INSTRUCTIONS:

1. Download mvp.py, COMPARESKILL.md, STOCKAGENT.md

2. Create .env file to store Open Router and Alpha Vantage API keys (create free accounts to demo for free)
  
3. Download necessary python packages. (May take trial and error or Codex to confirm all packages are sucessfully installed)

Running these commands will help install necessary packages

   python -m pip install python-dotenv
   
   python -m pip install flask python-dotenv openrouter
   
4. With all files in the same folder, run mvp.py and click on the link created in the terminal (if link fails run mvp.py again and it should work)

5. Interface with the stock agent with questions regarding stocks and stock performance and stock comparisons. (The current model can be a little language sensitive and may need extra clarificattion)
