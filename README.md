# Scroll Treasury Interface

Hey! Welcome to the Scroll DAO Treasury Tracker interface.

This is a simple Flask application created to track and visualise the finances of the Scroll DAO. It keeps an eye on our multisig wallets, tracks everyday expenses against our budgets, and lets us categorise transactions so we know exactly where the money is going.

It’s designed to be transparent for the community and easy to manage for the operations team.

## What It Does

*   **Treasury Dashboard:** Gives a view of all Scroll DAO assets, current balances in USD/SCR, and spending breakdowns.
*   **Multisig Tracking:** We monitor multiple wallets (Treasury, Operations Committee, Delegates, etc.) all in one place.
*   **Expense Categorisation:** The admin interface that allows the Operations Committee to log in and categorise transactions with specific budget categories and add notes if necessary.
*   **Budget vs. Actuals:** This section compares what has been spent against the semester budgets to keep DAO expenses on track.
*   **Auto-Updates:** Runs background jobs to fetch the latest transactions from Scrollscan so the data is always up-to-date.
*   **CSV Exports:** Everyone can export the transaction history for any multisig.

## Best Practices & Tech Stack

We kept it reliable and straightforward:
*   **Backend:** Python (Flask) - it’s robust and gets the job done.
*   **Database:** SQLite - simple, file-based, no heavy server setup required.
*   **Frontend:** Standard HTML/CSS/JS. No complex bundlers or frameworks, just clean code.
*   **Data Sources:** We hit the Scrollscan API for transactions and DefiLlama for token prices.

## Getting Started

Want to run this locally? Here is how you do it.

### Prerequisites

You'll need to install **Python 3.10+**.

### Installation

1.  **Clone the repo:**
    ```bash
    git clone https://github.com/Tiiinoo/scroll-treasury-interface/edit/main/README.md
    cd scroll-treasury
    ```

2.  **Set up a virtual environment** (keeps dependencies clean):
    ```bash
    python3 -m venv venv
    source venv/bin/activate  # On Windows use `venv\Scripts\activate`
    ```

3.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

### Configuration

You need to tell the app about our "secrets".
1.  Copy `.env.example` to a new file named `.env`.
    ```bash
    cp .env.example .env
    ```
2.  Open `.env` and fill in the details:
    *   `SECRET_KEY`: Random string for security.
    *   `AUTH_USERNAME` / `AUTH_PASSWORD`: Credentials for the admin interface.
    *   `SCROLLSCAN_API_KEY`: detailed tracking (optional but recommended).

### Running the App

Just run the main entry point:
```bash
python app.py
```

Head over to `http://localhost:8080` in your browser.
*   **Public Dashboard:** Accessible to everyone at `http://localhost:8080`.
*   **Login:** Go to `/login` to access admin features like categorisation.

## Project Structure

Here is a quick look at the files so you know your way around:
*   `app.py`: The heart of the app. Handles routes, API endpoints, and the server logic.
*   `config.py`: Where we define the multisigs, budget categories, and allocations. If you need to add a new quarterly budget, do it here.
*   `fetcher.py`: The worker script that speaks with the blockchain to get transaction data.
*   `models.py`: Database setup and schema definitions.
*   `templates/` & `static/`: The frontend UI code.

## Contributing

Found a bug? Want to add a cool chart? Feel free to open a PR or an issue. We try to keep the code clean and commented, so jump right in.
