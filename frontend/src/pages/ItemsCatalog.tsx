import { useState, useEffect } from 'react';
import type { ItemDTO } from '../types/api';
import { itemsService } from '../services/itemsService';
import { ItemCard } from '../components/features/ItemCard';
import { Button } from '../components/common/Button';
import { Search, Filter } from 'lucide-react';
import toast from 'react-hot-toast';
import { getApiErrorMessage } from '../utils/apiError';

export default function ItemsCatalog() {
  const [items, setItems] = useState<ItemDTO[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');

  useEffect(() => {
    const fetchItems = async () => {
      try {
        setIsLoading(true);
        const data = await itemsService.getAllItems();
        setItems(data);
      } catch (error: unknown) {
        const errorMessage = getApiErrorMessage(error, 'Failed to load items');
        toast.error(errorMessage);
        console.error('Error fetching items:', error);
      } finally {
        setIsLoading(false);
      }
    };

    fetchItems();

    return () => {
      toast.dismiss();
    };
  }, []);

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    toast.error('Search functionality coming soon!');
  };

  return (
    <div className="min-h-screen bg-gray-50">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-12">
        {/* Page Header */}
        <div className="mb-12">
          <h1 className="text-4xl font-bold text-gray-900 mb-4">
            Product Catalog
          </h1>
          <p className="text-xl text-gray-600">
            Discover our collection of quality products
          </p>
        </div>

        {/* Search and Filter Section */}
        <div className="bg-white rounded-lg shadow-md p-6 mb-8">
          <div className="grid md:grid-cols-2 gap-4">
            {/* Search Bar */}
            <form onSubmit={handleSearch}>
              <div className="relative">
                <Search className="absolute left-3 top-3 w-5 h-5 text-gray-400" />
                <input
                  type="text"
                  placeholder="Search products..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="input-field pl-10"
                />
              </div>
            </form>

            {/* Filter Button (UI only) */}
            <div className="flex items-center gap-2">
              <Button
                variant="secondary"
                size="md"
                className="flex items-center gap-2"
              >
                <Filter className="w-4 h-4" />
                <span>Filters</span>
              </Button>
              <span className="text-sm text-gray-500">
                Showing {items.length} items
              </span>
            </div>
          </div>
        </div>

        {/* Items Grid */}
        {isLoading ? (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {[...Array(6)].map((_, i) => (
              <div key={i} className="bg-white rounded-lg shadow-md p-6 animate-pulse">
                <div className="h-8 bg-gray-200 rounded mb-4 w-3/4"></div>
                <div className="h-4 bg-gray-200 rounded mb-3"></div>
                <div className="h-4 bg-gray-200 rounded mb-6 w-1/2"></div>
                <div className="h-10 bg-gray-200 rounded"></div>
              </div>
            ))}
          </div>
        ) : items.length === 0 ? (
          <div className="bg-white rounded-lg shadow-md p-12 text-center">
            <p className="text-xl text-gray-600 mb-4">No products found</p>
            <p className="text-gray-500">Please try adjusting your search criteria</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {items.map((item) => (
              <ItemCard key={item.id} item={item} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
