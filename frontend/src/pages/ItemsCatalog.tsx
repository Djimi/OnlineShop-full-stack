import { useState, useEffect, useMemo } from 'react';
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
  const [loadError, setLoadError] = useState<string | null>(null);
  const [reloadToken, setReloadToken] = useState(0);
  const [searchQuery, setSearchQuery] = useState('');
  const [showInStockOnly, setShowInStockOnly] = useState(false);

  const visibleItems = useMemo(() => {
    const query = searchQuery.trim().toLowerCase();

    return items.filter((item) => {
      const matchesSearch = !query || `${item.name} ${item.description}`.toLowerCase().includes(query);
      const matchesStock = !showInStockOnly || item.quantity > 0;
      return matchesSearch && matchesStock;
    });
  }, [items, searchQuery, showInStockOnly]);

  useEffect(() => {
    let isMounted = true;

    const fetchItems = async () => {
      try {
        setIsLoading(true);
        setLoadError(null);
        const data = await itemsService.getAllItems();
        if (isMounted) {
          setItems(data);
        }
      } catch (error: unknown) {
        const errorMessage = getApiErrorMessage(error, 'Failed to load items');
        if (isMounted) {
          setItems([]);
          setLoadError(errorMessage);
          toast.error(errorMessage);
        }
        console.error('Error fetching items:', error);
      } finally {
        if (isMounted) {
          setIsLoading(false);
        }
      }
    };

    fetchItems();

    return () => {
      isMounted = false;
      toast.dismiss();
    };
  }, [reloadToken]);

  const handleSearch = (e: React.FormEvent) => e.preventDefault();

  return (
    <main className="min-h-[calc(100vh-100px)]">
      <div className="max-w-7xl mx-auto px-6 md:px-16 py-16 md:py-24">
        {/* Page Header */}
        <div className="mb-12">
          <div className="eyebrow mb-5">The collection</div>
          <h1 className="font-display font-light text-5xl md:text-6xl leading-none tracking-[-0.015em] mb-5">
            Objects for a <em className="italic text-[#7a3b2c]">quieter</em> life.
          </h1>
          <p className="text-[#5b524a] text-base md:text-lg font-light max-w-xl leading-relaxed">
            Discover our collection of quality products, selected in small batches and made to last.
          </p>
        </div>

        {/* Search and Filter Section */}
        <div className="border-y border-[#dcd5c7] py-6 mb-12">
          <div className="grid md:grid-cols-[minmax(0,1fr)_auto] gap-5 items-center">
            {/* Search Bar */}
            <form onSubmit={handleSearch} className="relative">
              <label htmlFor="catalog-search" className="sr-only">Search products</label>
              <Search aria-hidden="true" className="absolute left-0 top-1/2 -translate-y-1/2 w-4 h-4 text-[#7a3b2c]" />
              <input
                id="catalog-search"
                type="search"
                placeholder="Search products..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="input-field pl-7"
              />
            </form>

            {/* Stock Filter */}
            <div className="flex items-center gap-4">
              <Button
                variant="secondary"
                size="md"
                type="button"
                aria-pressed={showInStockOnly}
                onClick={() => setShowInStockOnly((isEnabled) => !isEnabled)}
                className="flex items-center gap-2"
              >
                <Filter className="w-4 h-4" />
                <span>{showInStockOnly ? 'All products' : 'In stock only'}</span>
              </Button>
              <span className="text-xs tracking-[0.12em] uppercase text-[#5b524a] whitespace-nowrap">
                {visibleItems.length} of {items.length} shown
              </span>
            </div>
          </div>
        </div>

        {/* Items Grid */}
        {isLoading ? (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {[...Array(6)].map((_, i) => (
              <div key={i} aria-hidden="true" className="border border-[#dcd5c7] p-6 animate-pulse">
                <div className="h-7 bg-[#ede8dc] mb-5 w-3/4"></div>
                <div className="h-3 bg-[#ede8dc] mb-3"></div>
                <div className="h-3 bg-[#ede8dc] mb-8 w-1/2"></div>
                <div className="h-9 bg-[#ede8dc]"></div>
              </div>
            ))}
          </div>
        ) : loadError ? (
          <div role="alert" className="border border-[#dcd5c7] p-12 text-center">
            <p className="font-display text-3xl mb-3">The collection is unavailable.</p>
            <p className="text-[#5b524a] font-light mb-6">{loadError}</p>
            <Button type="button" onClick={() => setReloadToken((token) => token + 1)}>
              Try again
            </Button>
          </div>
        ) : visibleItems.length === 0 ? (
          <div className="border border-[#dcd5c7] p-12 text-center">
            <p className="font-display text-3xl mb-3">No products found.</p>
            <p className="text-[#5b524a] font-light">
              {items.length === 0 ? 'The collection is empty right now.' : 'Try a different search or show all products.'}
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {visibleItems.map((item) => (
              <ItemCard key={item.id} item={item} />
            ))}
          </div>
        )}
      </div>
    </main>
  );
}
