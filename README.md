# Ready-Aim-Learn-Final

A Django-based web application designed to facilitate shooting training sessions.([GitHub][1])

## Features will change 

* **User Authentication**: Secure login and registration system.
* **Instructor Dashboard**: Manage and schedule training sessions.
* **Student Dashboard**: View and book available training slots.
* **Booking System**: Real-time availability and booking management.
* **Responsive Design**: Optimized for both desktop and mobile devices.([GitHub][1])

## Installation

### Prerequisites

* Python 3.11 or higher
* Django 4.2 or higher
* SQLite3 (default database)

### Steps

1. **Clone the repository**:

   ```bash
   git clone https://github.com/VIDAKHOSHPEY22/Ready-Aim-Learn-Final.git
   cd Ready-Aim-Learn-Final
   ```



2. **Create and activate a virtual environment**:

   ```bash
   python -m venv venv
   # On Windows
   .\venv\Scripts\activate
   # On macOS/Linux
   source venv/bin/activate
   ```



3. **Install dependencies**:

   ```bash
   pip install -r requirements.txt
   ```



4. **Apply migrations**:

   ```bash
   python manage.py migrate
   ```



5. **Run the development server**:

   ```bash
   python manage.py runserver
   ```



Access the application at [http://127.0.0.1:8000](http://127.0.0.1:8000).

## Development

* **Testing**: Run tests using `python manage.py test`.
* **Static Files**: Collect static files with `python manage.py collectstatic`.
* **Environment Variables**: Configure sensitive settings in `.env` (ensure this file is excluded from version control).

## Contributing

Contributions are welcome! Please fork the repository, create a new branch, and submit a pull request.

## License

This project is licensed under the MIT License.
