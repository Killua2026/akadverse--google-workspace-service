import io
from googleapiclient.http import MediaIoBaseUpload
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

class AkadVerseDriveManager:
    def __init__(self, credentials):
        """Initializes the Google Drive API client."""
        self.creds = credentials
        self.service = build('drive', 'v3', credentials=self.creds)

    def get_or_create_folder(self, folder_name, parent_id=None):
        """
        Checks if a folder exists. If it does not, creates it.
        Returns the Folder ID.
        """
        try:
            # Build the query to search for the folder
            query = f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
            
            # If a parent_id is provided, look specifically inside that parent folder
            if parent_id:
                query += f" and '{parent_id}' in parents"
                
            # Execute the search
            results = self.service.files().list(
                q=query,
                spaces='drive',
                fields='files(id, name)'
            ).execute()
            
            files = results.get('files', [])
            
            # If the folder exists, return its ID
            if files:
                print(f"Folder '{folder_name}' already exists. ID: {files[0].get('id')}")
                return files[0].get('id')
                
            # If it does not exist, create it
            print(f"Creating new folder: '{folder_name}'...")
            folder_metadata = {
                'name': folder_name,
                'mimeType': 'application/vnd.google-apps.folder'
            }
            if parent_id:
                folder_metadata['parents'] = [parent_id]
                
            folder = self.service.files().create(
                body=folder_metadata,
                fields='id'
            ).execute()
            
            new_id = folder.get('id')
            print(f"Success: Folder '{folder_name}' created with ID: {new_id}")
            return new_id

        except HttpError as error:
            print(f"Google Drive API Error: {error}")
            return None
        except Exception as e:
            print(f"Unexpected error in folder creation: {e}")
            return None

    def setup_akadverse_structure(self, year="2026"):
        """
        Creates the nested structure: /AkadVerse/[Year]/Notes/
        Returns the ID of the 'Notes' folder.
        """
        print("Setting up AkadVerse folder structure...")
        
        # 1. Get or create root 'AkadVerse' folder
        root_id = self.get_or_create_folder("AkadVerse")
        if not root_id:
            return None
            
        # 2. Get or create the Year folder inside AkadVerse
        year_id = self.get_or_create_folder(year, parent_id=root_id)
        if not year_id:
            return None
            
        # 3. Get or create the 'Notes' folder inside the Year folder
        notes_id = self.get_or_create_folder("Notes", parent_id=year_id)
        
        return notes_id
    
    def create_note_doc(self, title, content, folder_id):
        """
        Creates a Google Doc directly inside the target folder using the provided text.
        We use the Drive API's multipart upload to auto-convert text to a Google Doc.
        """
        try:
            print(f"Uploading new note: '{title}'...")
            
            # The metadata tells Google to convert the upload into a native Google Doc
            # and places it directly into our specific folder ID
            file_metadata = {
                'name': title,
                'mimeType': 'application/vnd.google-apps.document',
                'parents': [folder_id]
            }

            # Convert the string content into a file-like byte stream for uploading
            media = MediaIoBaseUpload(
                io.BytesIO(content.encode('utf-8')),
                mimetype='text/plain',
                resumable=True
            )

            # Execute the upload
            file = self.service.files().create(
                body=file_metadata,
                media_body=media,
                fields='id, webViewLink'
            ).execute()

            print(f"Success: Note '{title}' created. Link: {file.get('webViewLink')}")
            return file.get('webViewLink')

        except HttpError as error:
            print(f"Google Drive API Error during upload: {error}")
            return None
        except Exception as e:
            print(f"Unexpected error creating doc: {e}")
            return None