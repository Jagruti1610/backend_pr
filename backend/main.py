import os
import json
from typing import Optional, List
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session, relationship
from dotenv import load_dotenv
import bcrypt
import jwt
from groq import Groq

# ---------- 1. LOAD ENVIRONMENT VARIABLES ----------
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

# ---------- 2. DATABASE ENGINE & SESSION ----------
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# ---------- 3. DATABASE TABLES ----------
class UserDB(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    role = Column(String(20), default="user")  # "user" or "admin"
    created_at = Column(String(50), default=str(datetime.now()))

    # Relationship: One user has many products
    products = relationship("ProductDB", back_populates="owner")

class ProductDB(Base):
    __tablename__ = "products"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    description = Column(String, nullable=True)
    price = Column(Float, nullable=False)
    quantity = Column(Integer, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    # Relationship: This product belongs to a user
    owner = relationship("UserDB", back_populates="products")

# Drop and recreate tables (WARNING: Deletes existing data!)
Base.metadata.drop_all(bind=engine)
Base.metadata.create_all(bind=engine)
print("✅ Database tables reset and ready!")

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

# ---------- 5. AUTH SETTINGS ----------
SECRET_KEY = "your-secret-key-here-change-this-in-production"  # CHANGE THIS!
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

# ---------- 6. PASSWORD HASHING ----------
def hash_password(password: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))

# ---------- 7. JWT TOKEN ----------
def create_access_token(data: dict, expires_delta: timedelta = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

# ---------- 8. GET CURRENT USER (DEPENDENCY) ----------
async def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: int = payload.get("user_id")
        if user_id is None:
            raise credentials_exception
    except jwt.PyJWTError:
        raise credentials_exception
    
    user = db.query(UserDB).filter(UserDB.id == user_id).first()
    if user is None:
        raise credentials_exception
    return user

# ---------- 9. FASTAPI APP & CORS ----------
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

# ---------- 10. DEPENDENCY: Get DB Session ----------
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ---------- 11. AUTH ENDPOINTS ----------
@app.post("/api/auth/signup")
async def signup(username: str, password: str, db: Session = Depends(get_db)):
    # Check if user exists
    existing_user = db.query(UserDB).filter(UserDB.username == username).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Username already taken")
    
    # Create new user (default role is "user")
    hashed_pw = hash_password(password)
    new_user = UserDB(username=username, hashed_password=hashed_pw, role="user")
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    
    # Create token
    token = create_access_token(data={"user_id": new_user.id, "role": new_user.role})
    return {"access_token": token, "token_type": "bearer", "role": new_user.role}

@app.post("/api/auth/login")
async def login(username: str, password: str, db: Session = Depends(get_db)):
    user = db.query(UserDB).filter(UserDB.username == username).first()
    if not user or not verify_password(password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    
    token = create_access_token(data={"user_id": user.id, "role": user.role})
    return {"access_token": token, "token_type": "bearer", "role": user.role}

# ---------- 12. CRUD ENDPOINTS (WITH AUTH) ----------
@app.get("/api/products", response_model=List[ProductResponse])
def get_all_products(db: Session = Depends(get_db), current_user: UserDB = Depends(get_current_user)):
    if current_user.role == "admin":
        # Admin can see everyone's products
        products = db.query(ProductDB).order_by(ProductDB.id).all()
    else:
        # Users only see their own products
        products = db.query(ProductDB).filter(ProductDB.user_id == current_user.id).order_by(ProductDB.id).all()
    return products

@app.post("/api/products", response_model=ProductResponse, status_code=201)
def create_product(product: ProductCreate, db: Session = Depends(get_db), current_user: UserDB = Depends(get_current_user)):
    db_product = ProductDB(**product.model_dump(), user_id=current_user.id)
    db.add(db_product)
    db.commit()
    db.refresh(db_product)
    return db_product

@app.get("/api/products/{product_id}", response_model=ProductResponse)
def get_product(product_id: int, db: Session = Depends(get_db), current_user: UserDB = Depends(get_current_user)):
    db_product = db.query(ProductDB).filter(ProductDB.id == product_id).first()
    if not db_product:
        raise HTTPException(status_code=404, detail="Product not found")
    
    # Authorization check
    if current_user.role != "admin" and db_product.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to view this product")
    return db_product

@app.put("/api/products/{product_id}", response_model=ProductResponse)
def update_product(product_id: int, product: ProductCreate, db: Session = Depends(get_db), current_user: UserDB = Depends(get_current_user)):
    db_product = db.query(ProductDB).filter(ProductDB.id == product_id).first()
    if not db_product:
        raise HTTPException(status_code=404, detail="Product not found")
    
    # Authorization check
    if current_user.role != "admin" and db_product.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to edit this product")
    
    db_product.name = product.name
    db_product.description = product.description
    db_product.price = product.price
    db_product.quantity = product.quantity
    db.commit()
    db.refresh(db_product)
    return db_product

@app.delete("/api/products/{product_id}", status_code=204)
def delete_product(product_id: int, db: Session = Depends(get_db), current_user: UserDB = Depends(get_current_user)):
    db_product = db.query(ProductDB).filter(ProductDB.id == product_id).first()
    if not db_product:
        raise HTTPException(status_code=404, detail="Product not found")
    
    # Authorization check
    if current_user.role != "admin" and db_product.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to delete this product")
    
    db.delete(db_product)
    db.commit()
    return None

# ---------- 14. ADMIN ENDPOINTS ----------
@app.get("/api/users")
async def get_all_users(db: Session = Depends(get_db), current_user: UserDB = Depends(get_current_user)):
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    users = db.query(UserDB).all()
    return [{"id": u.id, "username": u.username, "role": u.role, "created_at": u.created_at} for u in users]

@app.delete("/api/users/{user_id}")
async def delete_user(user_id: int, db: Session = Depends(get_db), current_user: UserDB = Depends(get_current_user)):
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    if current_user.id == user_id:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")
    user = db.query(UserDB).filter(UserDB.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    db.delete(user)
    db.commit()
    return {"message": "User deleted"}

@app.put("/api/users/{user_id}/role")
async def update_user_role(user_id: int, role: str, db: Session = Depends(get_db), current_user: UserDB = Depends(get_current_user)):
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    if role not in ["admin", "user"]:
        raise HTTPException(status_code=400, detail="Invalid role")
    user = db.query(UserDB).filter(UserDB.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.role = role
    db.commit()
    return {"message": f"User {user.username} role updated to {role}"}

# ---------- 13. GROQ AI ENDPOINT (SMART + STREAMING) ----------
@app.post("/api/chat/stream")
async def chat_stream(request: dict, current_user: UserDB = Depends(get_current_user)):
    try:
        user_message = request.get("message")
        inventory_data = request.get("inventory", [])

        if not user_message:
            raise HTTPException(status_code=400, detail="Message is required")

        # Build system prompt with inventory data
        system_prompt = f"""
You are an AI assistant for an inventory management app called "Aviraa". 
Here is the current list of products in the inventory (in JSON format): 
{json.dumps(inventory_data)}

Answer the user's questions based on this data. If they ask for calculations (like total value), do the math yourself and show the result clearly.
        """

        client = Groq()

        stream = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ],
            stream=True,
        )

        def generate():
            for chunk in stream:
                if chunk.choices[0].delta.content:
                    yield f"data: {json.dumps({'content': chunk.choices[0].delta.content})}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(generate(), media_type="text/event-stream")

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Groq API error: {str(e)}")