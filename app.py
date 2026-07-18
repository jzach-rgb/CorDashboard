from data_pipeline import load_and_clean
from datetime import date
from PIL import Image
from datetime import date, datetime
import plotly.graph_objects as go
import streamlit as st
import plotly.express as px
import pandas as pd
pd.set_option('display.max_columns', None)


sales_clean = load_and_clean([
    "item2019-2020.csv", "item2020-2021.csv", "item2021-2022.csv",
    "item2022-2023.csv", "item2023-2024.csv", "item2024-2025.csv",
    "item2025-2026.csv"])


st.set_page_config(layout="wide")  # lets all the graphs have room

st.markdown("""
    <style>
    .stApp {
        background-color: #E1B995;
    }
    </style>
""", unsafe_allow_html=True)

img = Image.open('cor.jpg')
st.image(img, width=200)

st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Poppins:wght@600;700&display=swap');

    .lmg-title {
        font-family: 'Poppins', sans-serif;
        font-weight: 700;
        font-size: 4.25rem;
        text-align: center;
        color: #8A382C;
    }
    </style>
    <div class="lmg-title">Cor Coffee Dashboard</div>
""", unsafe_allow_html=True)



cols = resizable_columns(2, border=True, key="kpi_row")

with cols[0]:
    st.metric("**Total Sales:**", sales_clean['Count'].sum())

with cols[1]:
    st.metric("**Total Profit:**", f"${sales_clean['Net Sales'].sum():,.2f}")


#milk, syrup = st.columns(2, border=True)

milkCounts = sales_clean['Milk'].value_counts()
fig = go.Figure(data=[go.Table(
        header=dict(values=['Milk', 'Sold'],
                    fill_color='#6FBAD2  ',
                    font=dict(color='white'),
                    align='left'),
        cells=dict(values=[sales_clean['Milk'].unique(),
                           milkCounts],
                   align='left'))
    ])
        
fig.update_layout(
        title='Milk Sold')
fig.update_layout(
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)')
st.plotly_chart(fig, use_container_width=False)