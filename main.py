import os
import shutil
import sys
import re
import asyncio
import subprocess
import aiohttp
from PyQt5.QtCore import *
from PyQt5.QtWidgets import *
from PyQt5.QtGui import *
from PyQt5 import uic

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
form_class, _ = uic.loadUiType(BASE_DIR + r'\stream_snatcher.ui')


def is_ffmpeg_installed():
    try:
        subprocess.run(
            ["ffmpeg", "-version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        return True
    except FileNotFoundError:
        return False


def is_url_valid(url):
    # Check if the URL contains the required pattern "(*)"
    return "(*)" in url


def is_referer_url_valid(referer_url):
    # Check if the referer URL is not an empty string
    return bool(referer_url.strip())


def is_folder_path_valid(folder_path):
    # Check if the folder path exists
    return os.path.exists(folder_path)


def is_file_name_valid(file_name):
    # Check if the referer URL is not an empty string
    return bool(file_name.strip())


class DownloadThread(QThread):
    # Signal to update the UI
    update_signal = pyqtSignal(str)
    completed_signal = pyqtSignal()

    def __init__(self, config):
        super().__init__()
        self.url = config['url']
        self.referer_url = config['referer_url']
        self.zero_padding = config['zero_padding']
        self.segment = config['segment']
        self.output_folder = config['output_folder']
        self.output_file_name = config['output_file_name']
        self.output_tmp_folder = f"{self.output_folder}/{self.output_file_name}_tmp"
        self.output_tmp_files_record = f"{self.output_tmp_folder}/files.txt"

    def concatenate_and_convert(self):
        # Create a list of all the .ts files
        ts_files = sorted([f for f in os.listdir(self.output_tmp_folder) if f.endswith('.ts')])

        # Create a list file (contains all .ts files' names)
        with open(self.output_tmp_files_record, "w", encoding="utf-8") as f:
            for ts_file in ts_files:
                f.write(f"file '{os.path.join(self.output_tmp_folder, ts_file)}'\n")

        # Use ffmpeg to concatenate all .ts files and convert to .mp4
        # Note: This assumes ffmpeg is installed and added to PATH
        try:
            output_file_full_path = self.output_folder + "/" + self.output_file_name + ".mp4"
            process = subprocess.run(
                ["ffmpeg", "-f", "concat", "-safe", "0",
                 "-i", self.output_tmp_files_record, "-c", "copy", output_file_full_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                creationflags=subprocess.CREATE_NO_WINDOW,
                check=True
            )
            self.update_signal.emit(f"{process.stderr}")
            self.update_signal.emit(f"Conversion complete: {output_file_full_path}")
        except subprocess.CalledProcessError as e:
            self.update_signal.emit(f"An error occurred: {str(e)}")
        finally:
            # Clean up temporary files
            os.remove(self.output_tmp_files_record)
            for ts_file in ts_files:
                os.remove(os.path.join(self.output_tmp_folder, ts_file))

            # Remove the temporary folder
            shutil.rmtree(self.output_tmp_folder)

            # Enable download button
            self.completed_signal.emit()

    async def download_file(self, session, i):
        file_name = f"{i:06d}.ts"
        file_path = os.path.join(self.output_tmp_folder, file_name)

        # Skip download if file already exists
        if os.path.exists(file_path):
            self.update_signal.emit(f"{file_name} already exists, skipping download.")
            return

        # Replace (*) in the URL with zero-padded segment number
        url = self.url.replace("(*)", str(i).zfill(self.zero_padding))

        self.update_signal.emit(f"Request URL: {url}")
        async with session.get(url, headers={"Referer": self.referer_url}) as response:
            if response.status == 200:
                with open(file_path, "wb") as f:
                    f.write(await response.read())
                self.update_signal.emit(f"Success to download {file_name}, status code: {response.status}")
            else:
                self.update_signal.emit(f"Failed to download {file_name}, status code: {response.status}")

    async def async_main(self):
        # Ensure the download folder exists
        os.makedirs(self.output_tmp_folder, exist_ok=True)

        conn = aiohttp.TCPConnector(limit=20)
        async with aiohttp.ClientSession(connector=conn) as session:
            tasks = [self.download_file(session, i) for i in
                     range(0, self.segment + 1)]
            await asyncio.gather(*tasks)

        self.update_signal.emit("Downloading finished, now converting...")
        self.concatenate_and_convert()

    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self.async_main())


class WindowClass(QMainWindow, form_class):
    def __init__(self):
        super().__init__()
        self.setupUi(self)
        self.setWindowIcon(QIcon(BASE_DIR + r'\stream_snatcher.ico'))

        # Fixed window size
        self.setFixedSize(self.width(), self.height())

        # Set input mask for the lineEdit_config_segment_videoLength
        self.lineEdit_config_segment_videoLength.setInputMask("99:99:99")

        # Clean file name to valid
        self.lineEdit_config_output_fileName.textChanged.connect(self.validate_filename)

        # Set radioButton toggle
        self.radioButton_config_segment_manualInput.toggled.connect(self.toggle_segment_radio)
        self.radioButton_config_segment_autoCalculation.toggled.connect(self.toggle_segment_radio)

        # Connect the button click event to the desired function
        self.pushButton_config_segment_apply.clicked.connect(self.calculate_and_set_segment_time)

        # Connect the button click event to the desired function
        self.pushButton_config_output_fileDialog.clicked.connect(self.select_folder)

        # Connect the button click event to the download function
        self.pushButton_download.clicked.connect(self.start_download)

        # Download thread
        self.download_thread = None

    def closeEvent(self, event):
        if not self.pushButton_download.isEnabled():
            reply = QMessageBox.question(
                self, 'Message', "Download is in progress. Are you sure to quit?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )

            if reply == QMessageBox.Yes:
                event.accept()
            else:
                event.ignore()
        else:
            event.accept()

    def log_message(self, message):
        # Get the current time and format it as hh:mm:ss
        current_time = QTime.currentTime().toString('hh:mm:ss')

        # Format the log message
        log_message = f"{current_time} - {message}"

        # Append the log message to the TextBrowser
        self.textBrowser_log.append(log_message)

    def validate_filename(self, text):
        # Regular expression to match any invalid filename characters
        invalid_filename_characters = re.compile(r'[<>:"/\\|?*]')

        # Check if the text contains any invalid characters
        if invalid_filename_characters.search(text):
            # Remove invalid characters
            cleaned_text = invalid_filename_characters.sub('', text)
            self.lineEdit_config_output_fileName.setText(cleaned_text)

    def toggle_segment_radio(self):
        # Check the status of the radio buttons and enable/disable fields accordingly
        manual_input_selected = self.radioButton_config_segment_manualInput.isChecked()
        auto_calculation_selected = self.radioButton_config_segment_autoCalculation.isChecked()

        # If Manual Input is selected
        self.lineEdit_config_segment_videoLength.setEnabled(auto_calculation_selected)
        self.spinBox_config_segment_timePerSegment.setEnabled(auto_calculation_selected)
        self.pushButton_config_segment_apply.setEnabled(auto_calculation_selected)
        # Add any other widgets you wish to enable/disable here

        # If Auto Calculation is selected
        self.spinBox_config_segment.setEnabled(manual_input_selected)

    def calculate_and_set_segment_time(self):
        # Get the time string from the lineEdit_config_segment_videoLength widget
        time_str = self.lineEdit_config_segment_videoLength.text()

        # Validate time string format
        time_pattern = re.compile(r"^\d{2}:\d{2}:\d{2}$")
        if not time_pattern.match(time_str):
            QMessageBox.warning(self, "Invalid Input", "Please enter a valid time format (hh:mm:ss).")
            return

        # Split the time string into hours, minutes, and seconds
        hours, minutes, seconds = map(int, time_str.split(':'))

        # Get the segment time from the spinBox_config_segment_timePerSegment widget
        segment_time = self.spinBox_config_segment_timePerSegment.value()

        # Convert everything to seconds and divide by the segment time
        total_segments = (hours * 3600) + (minutes * 60) + seconds
        segment_count = total_segments // segment_time

        # Set the calculated value to the spinBox_config_segment widget
        self.spinBox_config_segment.setValue(segment_count)

    def select_folder(self):
        # Open a QFileDialog in directory selection mode
        folder_path = QFileDialog.getExistingDirectory(self, "Select Folder")

        # Check if a folder was selected
        if folder_path:
            # Set the selected folder path to the lineEdit_config_output_folderPath widget
            self.lineEdit_config_output_folderPath.setText(folder_path)

    def start_download(self):
        # Input validation
        url = self.lineEdit_config_url.text()
        referer_url = self.lineEdit_config_refererUrl.text()
        folder_path = self.lineEdit_config_output_folderPath.text()
        file_name = self.lineEdit_config_output_fileName.text()

        if not is_url_valid(url):
            QMessageBox.warning(self, "Invalid URL",
                                "Please ensure the URL contains a (*) placeholder for segment numbers.")
            return

        if not is_referer_url_valid(referer_url):
            QMessageBox.warning(self, "Invalid Referer URL", "The referer URL cannot be empty.")
            return

        if not is_folder_path_valid(folder_path):
            QMessageBox.warning(self, "Invalid Folder Path", "Please select an existing folder.")
            return

        if not is_file_name_valid(file_name):
            QMessageBox.warning(self, "Invalid File Name Path", "The file name cannot be empty.")
            return

        config = {
            'url': url,
            'referer_url': referer_url,
            'zero_padding': self.spinBox_config_zeroPadding.value(),
            'segment': self.spinBox_config_segment.value(),
            'output_folder': folder_path,
            'output_file_name': file_name
        }

        self.pushButton_download.setEnabled(False)

        # Create and start the download thread
        self.download_thread = DownloadThread(config)
        self.download_thread.update_signal.connect(self.log_message)
        self.download_thread.completed_signal.connect(self.on_download_completed)
        self.download_thread.start()

    def on_download_completed(self):
        self.pushButton_download.setEnabled(True)


if __name__ == '__main__':
    app = QApplication(sys.argv)

    if not is_ffmpeg_installed():
        msg = QMessageBox()
        msg.setIcon(QMessageBox.Critical)
        msg.setText("FFmpeg is not installed")
        msg.setInformativeText("FFmpeg is not installed on your system. Please install FFmpeg to use this application.")
        msg.setWindowTitle("Error")
        msg.exec_()
        sys.exit(1)

    myWindow = WindowClass()
    myWindow.show()
    app.exec_()
