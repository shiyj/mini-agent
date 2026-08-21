import datetime

def main():
    now = datetime.datetime.now()
    print("Current date and time:", now.strftime("%Y-%m-%d %H:%M:%S"))
    print("Timezone:", now.tzname())

if __name__ == "__main__":
    main()