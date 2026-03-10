from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

class AkadVerseSheetManager:
    def __init__(self, credentials):
        self.creds = credentials
        # Build the Sheets Service
        self.service = build('sheets', 'v4', credentials=self.creds)

    def log_quiz_result(self, spreadsheet_id, course_name, score, grade):
        """
        Appends a new row to the specified Google Sheet.
        Format: [Date, Course, Score, Grade]
        """
        from datetime import datetime
        
        try:
            # Prepare the data row
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            values = [[now, course_name, score, grade]]
            body = {'values': values}
            
            # Use the append method to add to the end of the sheet
            # 'Sheet1!A1' is the range where it starts looking for empty rows
            result = self.service.spreadsheets().values().append(
                spreadsheetId=spreadsheet_id,
                range="Sheet1!A1",
                valueInputOption="RAW",
                body=body
            ).execute()
            
            print(f"Data logged to Sheet: {result.get('updates').get('updatedCells')} cells updated.")
            return True

        except HttpError as error:
            print(f"An error occurred: {error}")
            return False