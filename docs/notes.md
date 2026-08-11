# Learning notes

Notes written while building this project, originally kept as a comment block at the
bottom of `app.py`. They moved here when that file was split into the `attendance`
package, so the modules keep the comments that explain their own code while the longer
explanations live somewhere they can be read as prose.

Some of this describes the pre-package layout — `app.py` no longer exists, and the app is
now created by `create_app()` in `attendance/__init__.py` and served through `wsgi.py`.
The explanations of Flask, sessions, base64 and CSV are all still accurate.

---

If you run the file directly using:
    python app.py
then Python automatically sets __name__ to "__main__".
Because __name__ == "__main__", the condition becomes True,so app.run(debug=True) executes and starts the Flask server.
If another Python file imports this file:
    import app then __name__ becomes "app" instead of "__main__".
Since "app" != "__main__", the condition is False, so app.run() is NOT executed.
This prevents the server from starting automatically whenever app.py is imported into another module.
app.run(debug=True) starts Flask's built-in development web server.
debug=True enables Flask's debugger and automatically reloads the server whenever you save changes to your code.
It also provides detailed error pages with stack traces, making bugs much easier to find and fix during development.
Never enable debug=True in production because it exposes sensitive debugging information to users.

When you open http://127.0.0.1:5000/ in your browser, the browser sends an HTTP GET request to the Flask server.
Flask compares the requested URL against all registered routes.
If a route like @app.route("/") exists, Flask calls the corresponding function.
Decorators always begin with the @ symbol. A decorator modifies or registers the function immediately below it.
"If a user visits this URL, execute this function."
Whatever that function returns becomes the HTTP response sent back to the browser.
If the function returns a string such as: return "Hello, World!"
the browser simply displays that text.
render_template() is a Flask function that loads an HTML file
from the templates folder and sends it back as the HTTP response.

Example:
from flask import Flask, render_template
app = Flask(__name__)
@app.route("/")
def home():
    return render_template("index.html")
Instead of displaying plain text, the browser receives a complete HTML page and renders it as a normal website.
render_template() also allows Python variables to be inserted into HTML using Jinja templates.

Creating the Flask app is just like creating an object.
app = Flask(__name__) creates a Flask application object.  __name__ tells Flask where this application lives.
Flask uses this information to locate project resources, such as the templates and static folders.
JS Camera Capture
canvas.toDataURL("image/jpeg") converts the image currently drawn on the canvas into a Base64-encoded string.
Example output: data:image/jpeg;base64,/9j/4AAQSkZJRgABAQ...
The string has two parts. Everything before the comma is metadata describing the image format.
Example: data:image/jpeg;base64, Everything after the comma is the actual Base64-encoded image data.
JSON cannot contain raw binary image data directly. Converting the image into Base64 allows it to be safely included inside a JSON request.

fetch() is JavaScript's built-in function for sending HTTP requests.
In this project, fetch() sends a POST request from the browser to the Flask server.
POST is used because data is being sent to the server,rather than simply requesting a page.

Flask Request Handling
request represents the HTTP request received by Flask.
request.get_json() reads the JSON data sent by JavaScript.
The image string contains a metadata prefix before the actual Base64 image.
Example: data:image/jpeg;base64,/9j/4AAQ...
split(",", 1) removes the metadata portion, leaving only the Base64 image.
base64.b64decode(encoded) converts the Base64 text back into the original raw image bytes.
This confirms the image survived the HTTP request without corruption.

Sessions and Redirects
session stores information about the currently logged-in user.
flask stores this information securely using the application's secret key.
redirect() tells the browser to immediately request another URL.
It is commonly used after login or logout.

Example:
User logs in successfully.
Flask redirects the browser to the dashboard page.

Face Recognition Workflow
User opens the camera webpage. Flask serves camera.html.
Js accesses the webcam and captures an image from the video stream.
The image is converted into a Base64 string.
JS sends the image to Flask using a POST request.
Flask decodes the Base64 image.
OpenCV detects faces in the image.
The face recognition model identifies the person. Flask returns the person's name as JSON.
js displays the recognised name on the webpage. jsonify() converts a Python dictionary into
JSON so that js can easily read the response.

OpenCV (cv2) processes images. openCV images are stored as NumPy arrays, 
which is why NumPy is required.

SQLite and CSV Export
SQLite is excellent for storing and querying application data.
However, SQLite database files are binary files.They are not designed to be opened directly by teachers,
administrators, or other non-technical users.
CSV is a universal file format supported by Excel, Google Sheets, LibreOffice, and many other programs.
Exporting attendance as CSV allows anyone to view the records without special software.
The exported CSV is only a snapshot of the database at the moment the Export button is clicked.
If new attendance records are added later,the downloaded CSV does not update automatically.
The real source of truth always remains attendance.db.
Functions such as mark_attendance(), get_todays_attendance(),and get_all_attendance()
always read the latest information directly from the database.
The CSV is only a one-way export. editing the CSV file does not change the database.
This prevents inconsistencies between exported reports and the application's actual data.
HTTP provides a built-in mechanism for file downloadsusing the Content-Disposition response header.
Setting:
Content-Disposition: attachment tells the browser to download the response as a file
instead of displaying it in the browser.

Why use io.StringIO()?
io.StringIO() creates a file-like object entirely in memory.
The CSV is generated in RAM instead of being written to a temporary file on disk.
This avoids several problems: No temporary files need to be created.
Multiple users can export simultaneously without filename conflicts.
No cleanup code is required to delete temporary files.
The CSV exists only while it is being generated and sent to the browser.
Once the response is sent,the in-memory object is discarded automatically.