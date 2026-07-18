"""
Cor Coffee sales data pipeline
Compiled from eda.ipynb — cleans raw Square POS exports into sales_clean,
ready for Streamlit dashboard use.

Fixes applied vs. the original notebook:
  1. Flavor/syrup merge now APPENDS to Modifiers Applied instead of overwriting it
     (previously wiped out milk/here-to-go/caffeine info for ~587 rows)
  2. Breve Special's Half&Half syrup tag now set AFTER the full Syrup column
     assignment, so it doesn't get overwritten
  3. Half-Caf added as its own Caffeine bucket (previously silently defaulted to 'Cafe')
"""

import pandas as pd
import numpy as np
from datetime import datetime, date

pd.set_option('display.max_columns', None)


def load_and_clean(csv_paths):
    """
    csv_paths: list of file paths to the yearly Square export CSVs, in any order.
    Returns: sales_clean, the fully processed DataFrame.
    """

    # ---------------------------------------------------------------
    # 1. Load + combine
    # ---------------------------------------------------------------
    frames = [pd.read_csv(p) for p in csv_paths]
    sales = pd.concat(frames, ignore_index=True)

    # Drop rows with no Price Point Name (unspecified drinks, pre-Cor-Coffee data)
    sales = sales.dropna(subset=['Price Point Name'])

    # ---------------------------------------------------------------
    # 2. Drop unhelpful columns
    # ---------------------------------------------------------------
    sales_clean = sales.drop(columns=[
        'Time Zone', 'Price Point Name', 'Tax', 'Customer ID', 'Qty',
        'Payment ID', 'SKU', 'Customer Name', 'Customer Reference ID',
        'Device Name', 'Unit', 'Details', 'Event Type', 'Channel',
        'Location', 'Fulfillment Note', 'Dining Option',
        'Itemization Type', 'Card Brand'
    ])

    # ---------------------------------------------------------------
    # 3. Type conversions
    # ---------------------------------------------------------------
    sales_clean['Date'] = pd.to_datetime(sales_clean['Date'])
    sales_clean['Time'] = pd.to_datetime(sales_clean['Time'], format='%H:%M:%S').dt.time

    for col in ['Gross Sales', 'Discounts', 'Net Sales']:
        sales_clean[col] = sales_clean[col].replace(r'[\$,]', '', regex=True).astype(float)

    sales_clean['Category'] = sales_clean['Category'].astype('category')
    sales_clean['Item'] = sales_clean['Item'].astype('category')

    # ---------------------------------------------------------------
    # 4. Temp (Hot/Iced) — must run before the flavor merge below,
    #    since Temp is a real column by that point and isn't affected
    #    by the Modifiers Applied edits that follow.
    # ---------------------------------------------------------------
    sales_clean['Temp'] = np.where(
        sales_clean['Modifiers Applied'].str.contains("iced", case=False, na=False),
        "Iced", "Hot"
    )

    iced_item_fixes = {
        'Iced Latte': 'Latte',
        'Iced Americano': 'Americano',
        'Iced Mocha': 'Mocha',
        'Iced Tea': 'Tea',
        'Iced Espresso': 'Espresso',
        'Iced Chai Latte': 'Chai Latte',
    }
    for old_item, new_item in iced_item_fixes.items():
        sales_clean.loc[sales_clean["Item"] == old_item, "Temp"] = "Iced"
        sales_clean.loc[sales_clean["Item"] == old_item, "Item"] = new_item

    # Breve Special emoji variant
    sales_clean.loc[sales_clean["Item"] == 'Breve Special \U0001F618', "Item"] = 'Breve Special'
    sales_clean['Item'] = sales_clean['Item'].cat.remove_unused_categories()

    # ---------------------------------------------------------------
    # 5. Flavor rows -> merge into their paired drink row
    #    (adjacency + <2s time-gap match, validated at 587/588 in EDA)
    # ---------------------------------------------------------------
    sales_clean = sales_clean.reset_index(drop=True)
    flavor_rows = sales_clean[sales_clean["Item"].str.contains("flavor", case=False)].index

    paired_with = {}
    for i in flavor_rows:
        has_next = (i + 1) in sales_clean.index
        has_prev = (i - 1) in sales_clean.index and i - 1 >= 0

        diff_next = diff_prev = None
        if has_next:
            t_i = datetime.combine(date.today(), sales_clean.loc[i, 'Time'])
            t_next = datetime.combine(date.today(), sales_clean.loc[i + 1, 'Time'])
            diff_next = abs((t_next - t_i).total_seconds())
        if has_prev:
            t_prev = datetime.combine(date.today(), sales_clean.loc[i - 1, 'Time'])
            t_i = datetime.combine(date.today(), sales_clean.loc[i, 'Time'])
            diff_prev = abs((t_i - t_prev).total_seconds())

        candidates = []
        if diff_next is not None and diff_next < 2:
            candidates.append((diff_next, i + 1))
        if diff_prev is not None and diff_prev < 2:
            candidates.append((diff_prev, i - 1))

        if candidates:
            candidates.sort()
            paired_with[i] = candidates[0][1]

    # Drop the one known outlier flavor row that never matched (70s gap, no clean pair)
    matched_indices = set(paired_with.keys())
    unmatched = flavor_rows.difference(matched_indices)
    sales_clean = sales_clean.drop(index=unmatched)

    # Merge each flavor row into its paired drink row
    # FIX: append the flavor onto existing modifiers instead of overwriting them
    for key, value in paired_with.items():
        flavor = sales_clean.loc[key, "Item"][:-7]  # strip trailing " Flavor"
        gSales = sales_clean.loc[key, "Gross Sales"] + sales_clean.loc[value, "Gross Sales"]
        discount = sales_clean.loc[key, "Discounts"] + sales_clean.loc[value, "Discounts"]
        nSales = sales_clean.loc[key, "Net Sales"] + sales_clean.loc[value, "Net Sales"]

        existing_mods = sales_clean.loc[value, "Modifiers Applied"]
        sales_clean.loc[value, "Modifiers Applied"] = (
            existing_mods + ", " + flavor if pd.notna(existing_mods) else flavor
        )
        sales_clean.loc[value, "Gross Sales"] = gSales
        sales_clean.loc[value, "Discounts"] = discount
        sales_clean.loc[value, "Net Sales"] = nSales

    sales_clean = sales_clean.drop(index=list(paired_with.keys()))
    sales_clean = sales_clean.reset_index(drop=True)

    # ---------------------------------------------------------------
    # 6. Standalone milk/extra-shot rows -> merge into paired drink
    #    (same Transaction ID logic, more reliable than adjacency for
    #    orders with more than 2 items)
    # ---------------------------------------------------------------
    def merge_standalone_item(df, item_name, modifier_text):
        for i in df[df['Item'] == item_name].index:
            drinkRow = df[
                (df['Transaction ID'] == df.loc[i, 'Transaction ID']) & (df.index != i)
            ].index
            if len(drinkRow) == 0:
                continue

            df.loc[drinkRow, 'Modifiers Applied'] = (
                df.loc[drinkRow, 'Modifiers Applied'].fillna('') + ", " + modifier_text
            )

            gSale = df.loc[i, 'Gross Sales']
            discount = df.loc[i, 'Discounts']
            nSale = df.loc[i, 'Net Sales']

            df.loc[drinkRow, 'Gross Sales'] += gSale
            df.loc[drinkRow, 'Discounts'] += discount
            df.loc[drinkRow, 'Net Sales'] += nSale

        return df.drop(index=df[df['Item'] == item_name].index)

    sales_clean = merge_standalone_item(sales_clean, 'Almond Milk', 'Almond')
    sales_clean = merge_standalone_item(sales_clean, 'Oat milk', 'Oat')
    sales_clean = merge_standalone_item(sales_clean, 'Extra shot', 'Extra shot')
    sales_clean = sales_clean.reset_index(drop=True)

    # ---------------------------------------------------------------
    # 7. Item name consolidation (spelling / naming variants)
    # ---------------------------------------------------------------
    rename_map = {
        "Capuccino": "Cappuccino",
        "Hot chocolate": "Hot Chocolate",
        "Hot Chocolate ": "Hot Chocolate",
        "Hot Cocoa": "Hot Chocolate",
        "Affagado": "Affogato",
        "Taos Granola Bar": "Taos Bar",
        "Mexican Mocha": "Mocha",
        "Mexican Hot Cocoa": "Hot Chocolate",
        "T-shirt": "Tshirt",
        "Pullover Sweater": "Pullover",
    }
    for old, new in rename_map.items():
        sales_clean.loc[sales_clean['Item'] == old, "Item"] = new

    # Cookie deals — preserve quantity in Count before consolidating name
    sales_clean.loc[sales_clean['Item'] == "3 cookie deal", "Count"] = 3
    sales_clean.loc[sales_clean['Item'] == "3 cookie deal", "Item"] = "Cookie"
    sales_clean.loc[sales_clean['Item'] == "2 cookie deal", "Count"] = 2
    sales_clean.loc[sales_clean['Item'] == "2 cookie deal", "Item"] = "Cookie"
    sales_clean.loc[sales_clean['Item'] == "LiaP Cookie", "Item"] = "Cookie"

    # Drop Baked good (only 32 rows, $1 each, not worth tracking)
    sales_clean = sales_clean.drop(
        index=sales_clean[sales_clean['Item'] == 'Baked good'].index
    )

    # Dirty Chai hiding as "Chai Latte" + extra shot
    sales_clean.loc[
        (sales_clean["Item"] == "Chai Latte") &
        (sales_clean["Modifiers Applied"].str.contains("Shot", case=False, na=False)),
        "Item"
    ] = "Dirty Chai"

    # ---------------------------------------------------------------
    # 8. Named drinks that are really [base item] + [flavor modifier]
    #    Each: fold the flavor into Modifiers Applied (append-safe),
    #    then rename Item down to its base drink.
    # ---------------------------------------------------------------
    flavor_fold = {
        "Lavender Latte":            ("Lavender",   "Latte"),
        "Lavlem":                    ("Lavender",   "Lemonade"),
        "Cafe Miel":                 ("Cafe Miel",  "Latte"),
        "Peppermint Hot Cocoa":      ("Peppermint", "Hot Chocolate"),
        "Peppermint Mocha":          ("Peppermint", "Mocha"),
        "Gingerbread Latte":         ("Gingerbread","Latte"),
        "Pumpkin Pie Latte":         ("Pumpkin",    "Latte"),
        "Peppermint Hot Chocolate":  ("Peppermint", "Hot Chocolate"),
        "Summit S'mores Latte":      ("S'Mores",    "Latte"),
    }

    for item_name, (modifier, base_item) in flavor_fold.items():
        mask = sales_clean['Item'] == item_name
        mods = sales_clean.loc[mask, "Modifiers Applied"]

        sales_clean.loc[mask, "Modifiers Applied"] = np.where(
            mods.isna() | mods.str.contains(modifier, case=False, na=False),
            mods.fillna(modifier),
            mods + ", " + modifier
        )
        sales_clean.loc[sales_clean['Item'] == item_name, "Item"] = base_item

    sales_clean['Item'] = sales_clean['Item'].cat.remove_unused_categories()

    # ---------------------------------------------------------------
    # 9. Derived feature columns (all read from Modifiers Applied,
    #    which by this point has every fold/append already applied)
    # ---------------------------------------------------------------

    # To go / Here
    sales_clean['To Go'] = np.where(
        sales_clean['Modifiers Applied'].str.contains("Here", case=False, na=False),
        False, True
    )

    # Milk type
    milk_types = ['Oat', 'Almond', 'Soy', 'Coconut', 'Half&Half', 'Whole']
    conditions = [
        sales_clean['Modifiers Applied'].str.contains(m, case=False, na=False)
        for m in milk_types
    ]
    sales_clean['Milk'] = np.select(conditions, milk_types, default='Whole')

    # Breve Special is always made with half & half, regardless of what
    # Modifiers Applied says (it's baked into the drink, not a customer modifier)
    sales_clean.loc[sales_clean['Item'] == 'Breve Special', 'Milk'] = 'Half&Half'

    # Caffeine level  (FIX: Half-Caf added — previously fell through to 'Cafe')
    caffeine_types = ['Decaf', 'Half-Caf']
    conditions = [
        sales_clean['Modifiers Applied'].str.contains(c, case=False, na=False)
        for c in caffeine_types
    ]
    sales_clean['Caffeine'] = np.select(conditions, caffeine_types, default='Cafe')

    # Sweetness
    sweet_types = ['Regular', 'Sweet']
    conditions = [
        sales_clean['Modifiers Applied'].str.contains(s, case=False, na=False)
        for s in sweet_types
    ]
    sales_clean['Sweetness'] = np.select(conditions, sweet_types, default='Regular')

    # Syrup
    syrup_types = [
        'Vanilla', 'Lavender', 'Hazelnut', 'Strawberry', 'Rose', 'Ube', 'Pistachio',
        'Peppermint', 'Gingerbread', 'Pumpkin', 'Apple', 'Sugar cookie', 'Ganache',
        'Miel', 'Cor De Hanoi', "S'mores"
    ]
    conditions = [
        sales_clean['Modifiers Applied'].str.contains(s, case=False, na=False)
        for s in syrup_types
    ]
    sales_clean['Syrup'] = np.select(conditions, syrup_types, default="NA")

    # Hard-coded syrup overrides (must run AFTER the np.select above,
    # since np.select overwrites the whole column)
    sales_clean.loc[
        (sales_clean['Item'] == 'Hot Chocolate') | (sales_clean['Item'] == 'Mocha'),
        'Syrup'
    ] = 'Ganache'
    sales_clean.loc[sales_clean['Item'] == 'London Fog', 'Syrup'] = 'Vanilla'

    # ---------------------------------------------------------------
    # 10. Category rebuild (from cleaned Item list, not the original
    #     unreliable Category column)
    # ---------------------------------------------------------------
    espresso_drinks = ['Latte', 'Cappuccino', 'Americano', 'Espresso', 'Flat White',
                        'Mocha', 'Dirty Chai', 'Macchiato', 'Affogato', 'Cortado',
                        'Cafe Au Lait', 'Red Eye', 'Breve Special']

    food_items = ['Kind Bar', 'Cookie', 'Taos Bar']

    merch_items = ['Sticker', 'Beanie', 'Tshirt', 'Pullover', 'Travel Mug', 'Tote bag',
                   'Catholic Flames Shirt', 'Flowers', 'Cap']

    non_espresso_drinks = ['Coffee', 'Chai Latte', 'Matcha Latte', 'Tea', 'Hot Chocolate',
                            'Lemonade', 'Steamer', 'Cold Brew', 'London Fog',
                            'Matcha lemonade', 'Soda']

    conditions = [
        sales_clean['Item'].isin(espresso_drinks),
        sales_clean['Item'].isin(food_items),
        sales_clean['Item'].isin(merch_items),
        sales_clean['Item'].isin(non_espresso_drinks),
    ]
    choices = ['Espresso Drinks', 'Food', 'Merch', 'Non-Espresso Drinks']
    sales_clean['Category'] = np.select(conditions, choices, default='CHECK')

    return sales_clean


if __name__ == "__main__":
    csv_paths = [
        "item2019-2020.csv", "item2020-2021.csv", "item2021-2022.csv",
        "item2022-2023.csv", "item2023-2024.csv", "item2024-2025.csv",
        "item2025-2026.csv",
    ]
    sales_clean = load_and_clean(csv_paths)
    print(sales_clean.shape)
    print(sales_clean['Category'].value_counts())
    # Should show zero CHECK rows if the item list is fully accounted for
    print("Uncategorized rows:", len(sales_clean[sales_clean['Category'] == 'CHECK']))
