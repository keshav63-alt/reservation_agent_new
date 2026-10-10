import os 
from dotenv import load_dotenv
from supabase import create_client , Client
load_dotenv(override=True)

url = os.getenv("SUPABASE_DATA_URL")
api = os.getenv("SUPABASE_PUBLIC_API")

supabase:Client  = create_client(url,api)

try:
     data = {
          "title":"mkea",
          "content":"kas"
          
     }
     response = supabase.table("posts").create(data).execute(  )
     print("table create sucessfully new row",response.data)
except Exception as e:
     print("error",e)

# class ReastaurantDatabase:
#      pass 


  