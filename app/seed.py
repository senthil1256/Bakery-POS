"""Demo bakery menu so the app is never empty on first run."""
from .money import to_cents
from .services import PosService

DEMO_MENU = [
    # (category, name, price, discount %)
    ("Bread", "Sourdough Loaf", "8.50", 0),
    ("Bread", "Baguette", "4.00", 0),
    ("Bread", "Multigrain Loaf", "6.80", 0),
    ("Pastries", "Butter Croissant", "3.50", 0),
    ("Pastries", "Pain au Chocolat", "4.20", 0),
    ("Pastries", "Cinnamon Roll", "4.50", 0),
    ("Pastries", "Cheese Danish", "4.00", 10),
    ("Pastries", "Blueberry Muffin", "3.80", 0),
    ("Cakes", "Chocolate Cake Slice", "6.50", 0),
    ("Cakes", "Cheesecake Slice", "7.00", 0),
    ("Cakes", "Whole Chocolate Cake", "48.00", 0),
    ("Drinks", "Latte", "5.00", 0),
    ("Drinks", "Americano", "4.00", 0),
    ("Drinks", "Iced Tea", "3.50", 0),
]


def seed_demo_menu(service: PosService) -> bool:
    """Fill the menu with demo items if it is empty. Returns True if it did."""
    if service.list_menu():
        return False
    for category, name, price, discount in DEMO_MENU:
        service.add_menu_item(name, category, to_cents(price), discount)
    return True
