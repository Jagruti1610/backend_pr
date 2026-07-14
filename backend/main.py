import os
from typing import Optional, List
from fastapi import FastAPI, HTTPException, Depends  # <-- Added Depends here!
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session  # <-- Added Session here!
from dotenv import load_dotenv
from groq import Groq

# ---------- 1. LOAD ENVIRONMENT VARIABLES ----------
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

# ---------- 2. DATABASE ENGINE & SESSION ----------
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# ---------- 3. DATABASE TABLE (SQLAlchemy Model) ----------
class ProductDB(Base):
    __tablename__ = "products"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    description = Column(String, nullable=True)
    price = Column(Float, nullable=False)
    quantity = Column(Integer, nullable=False)

# Create table if it doesn't exist
Base.metadata.create_all(bind=engine)

# ---------- 4. PYDANTIC SCHEMAS ----------
class ProductCreate(BaseModel):
    name: str
    description: Optional[str] = None
    price: float
    quantity: int

class ProductResponse(BaseModel):
    id: int
    name: str
    description: Optional[str]
    price: float
    quantity: int

    class Config:
        from_attributes = True

# ---------- 5. FASTAPI APP & CORS ----------
app = FastAPI(title="Aviraa Inventory API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
    "http://localhost:5173",
    "https://frontend-pr.vercel.app"
],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- 6. DEPENDENCY: Get DB Session ----------
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ---------- 7. CRUD ENDPOINTS (FIXED) ----------

# GET all products
@app.get("/api/products", response_model=List[ProductResponse])
def get_all_products(db: Session = Depends(get_db)):  # <-- FIXED: Added Depends
    products = db.query(ProductDB).order_by(ProductDB.id).all()
    return products

# GET single product by ID
@app.get("/api/products/{product_id}", response_model=ProductResponse)
def get_product(product_id: int, db: Session = Depends(get_db)):  # <-- FIXED: Added Depends
    product = db.query(ProductDB).filter(ProductDB.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return product

# POST a new product
@app.post("/api/products", response_model=ProductResponse, status_code=201)
def create_product(product: ProductCreate, db: Session = Depends(get_db)):  # <-- FIXED: Added Depends
    db_product = ProductDB(**product.model_dump())
    db.add(db_product)
    db.commit()
    db.refresh(db_product)
    return db_product

# PUT update an existing product
@app.put("/api/products/{product_id}", response_model=ProductResponse)
def update_product(product_id: int, product: ProductCreate, db: Session = Depends(get_db)):  # <-- FIXED: Added Depends
    db_product = db.query(ProductDB).filter(ProductDB.id == product_id).first()
    if not db_product:
        raise HTTPException(status_code=404, detail="Product not found")
    
    db_product.name = product.name
    db_product.description = product.description
    db_product.price = product.price
    db_product.quantity = product.quantity
    
    db.commit()
    db.refresh(db_product)
    return db_product

# DELETE a product
@app.delete("/api/products/{product_id}", status_code=204)
def delete_product(product_id: int, db: Session = Depends(get_db)):  # <-- FIXED: Added Depends
    db_product = db.query(ProductDB).filter(ProductDB.id == product_id).first()
    if not db_product:
        raise HTTPException(status_code=404, detail="Product not found")
    db.delete(db_product)
    db.commit()
    return None


# ---------- 8. GROQ AI ENDPOINT ----------
@app.post("/api/chat")
async def chat_with_groq(request: dict):
    try:
        user_message = request.get("message")
        if not user_message:
            raise HTTPException(status_code=400, detail="Message is required")

        # Initialize Groq (it automatically looks for GROQ_API_KEY in environment)
        client = Groq()

        chat_completion = client.chat.completions.create(
            messages=[
                {
                    "role": "user",
                    "content": user_message,
                }
            ],
            model="llama-3.3-70b-versatile",  # You can change this later
        )

        ai_response = chat_completion.choices[0].message.content
        return {"response": ai_response}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Groq API error: {str(e)}")