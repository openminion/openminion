import SwiftUI

struct ContentView: View {
    @State private var query = ""

    var filteredItems: [String] {
        (0..<10_000).map(String.init).filter { query.isEmpty || $0.contains(query) }
    }

    var body: some View {
        VStack {
            TextField("Filter", text: $query)
            List(filteredItems, id: \.self) { item in
                Text(item)
            }
        }
    }
}
