"""
SQLite Database Query Tool
Run SQL queries against the k8sai.db database
"""
import sqlite3
import sys

def run_query(query):
    """Execute a SQL query and display results"""
    try:
        conn = sqlite3.connect('k8sai.db')
        cursor = conn.cursor()
        cursor.execute(query)
        
        # Get column names
        columns = [description[0] for description in cursor.description] if cursor.description else []
        
        # Get results
        results = cursor.fetchall()
        
        if columns:
            # Print header
            print("\n" + " | ".join(f"{col:<20}" for col in columns))
            print("-" * (len(columns) * 23))
            
            # Print rows
            for row in results:
                print(" | ".join(f"{str(val):<20}" for val in row))
            
            print(f"\nTotal rows: {len(results)}")
        else:
            print(f"Query executed successfully. Rows affected: {cursor.rowcount}")
        
        conn.commit()
        conn.close()
        
    except sqlite3.Error as e:
        print(f"SQLite error: {e}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python query_db.py \"YOUR SQL QUERY\"")
        print("\nExample queries:")
        print('  python query_db.py "SELECT * FROM users"')
        print('  python query_db.py "SELECT username, role FROM users WHERE is_active=1"')
        print('  python query_db.py "SELECT name FROM sqlite_master WHERE type=\'table\'"')
    else:
        query = sys.argv[1]
        run_query(query)
