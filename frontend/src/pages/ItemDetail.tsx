import { useState, useEffect } from 'react';
import { useParams, useNavigate, Link } from 'react-router';
import type { ItemDTO } from '../types/api';
import { itemsService } from '../services/itemsService';
import { Button } from '../components/common/Button';
import { ArrowLeft, AlertCircle, CheckCircle, Package } from 'lucide-react';
import toast from 'react-hot-toast';
import { getApiErrorMessage } from '../utils/apiError';

export default function ItemDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [item, setItem] = useState<ItemDTO | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchItem = async () => {
      if (!id) {
        setError('Item ID not found');
        setIsLoading(false);
        return;
      }

      try {
        setIsLoading(true);
        setError(null);
        setItem(null);
        const data = await itemsService.getItemById(id);
        setItem(data);
      } catch (error: unknown) {
        const errorMessage = getApiErrorMessage(error, 'Failed to load item details');
        setError(errorMessage);
        toast.error(errorMessage);
        console.error('Error fetching item:', error);
      } finally {
        setIsLoading(false);
      }
    };

    fetchItem();

    return () => {
      toast.dismiss();
    };
  }, [id]);

  const isOutOfStock = item ? item.quantity <= 0 : false;

  return (
    <main className="min-h-[calc(100vh-100px)]">
      <div className="max-w-5xl mx-auto px-6 md:px-16 py-12 md:py-20">
        {/* Back Button */}
        <button
          type="button"
          onClick={() => navigate('/items')}
          className="nav-link flex items-center gap-2 hover:text-[#7a3b2c] mb-10"
        >
          <ArrowLeft aria-hidden="true" className="w-4 h-4" />
          <span>Back to Catalog</span>
        </button>

        {/* Breadcrumbs */}
        <div className="text-xs tracking-[0.12em] uppercase text-[#5b524a] mb-8">
          <Link to="/items" className="hover:text-[#7a3b2c]">Catalog</Link>
          <span className="mx-2" aria-hidden="true">/</span>
          <span className="text-[#1f1a14]">
            {item?.name || 'Loading...'}
          </span>
        </div>

        {/* Loading State */}
        {isLoading && (
          <div role="status" className="border border-[#dcd5c7] p-12 text-center">
            <div aria-hidden="true" className="animate-spin rounded-full h-10 w-10 border-2 border-[#dcd5c7] border-t-[#7a3b2c] mx-auto mb-4"></div>
            <p className="text-[#5b524a] font-light">Loading item details...</p>
          </div>
        )}

        {/* Error State */}
        {error && !isLoading && (
          <div role="alert" className="border border-[#dcd5c7] p-8 text-center">
            <AlertCircle aria-hidden="true" className="w-12 h-12 text-[#7a3b2c] mx-auto mb-4" />
            <h2 className="font-display text-4xl mb-2">
              Oops! Something went wrong
            </h2>
            <p className="text-[#5b524a] font-light mb-6">{error}</p>
            <Button
              variant="primary"
              onClick={() => navigate('/items')}
            >
              Return to Catalog
            </Button>
          </div>
        )}

        {/* Item Details */}
        {item && !isLoading && (
          <div className="border border-[#dcd5c7] overflow-hidden">
            <div className="grid md:grid-cols-2 gap-10 p-6 md:p-10">
              {/* Left Column - Product Image Placeholder */}
              <div className="flex items-center justify-center">
                <div className="w-full aspect-square bg-gradient-to-br from-[#e8e1d3] to-[#dcd2bf] flex items-center justify-center">
                  <Package aria-hidden="true" className="w-32 h-32 text-[#7a3b2c]/50" />
                </div>
              </div>

              {/* Right Column - Details */}
              <div className="flex flex-col justify-between">
                {/* Product Info */}
                <div>
                  <h1 className="font-display font-light text-5xl leading-none mb-5">
                    {item.name}
                  </h1>

                  {/* Stock Status */}
                  <div className="mb-6">
                    {isOutOfStock ? (
                      <div className="flex items-center gap-2 text-[#7a3b2c] border-y border-[#dcd5c7] px-1 py-3">
                        <AlertCircle aria-hidden="true" className="w-5 h-5" />
                        <span className="font-medium">Out of Stock</span>
                      </div>
                    ) : (
                      <div className="flex items-center gap-2 text-[#5b524a] border-y border-[#dcd5c7] px-1 py-3">
                        <CheckCircle aria-hidden="true" className="w-5 h-5 text-[#7a3b2c]" />
                        <span className="font-medium">
                          In Stock ({item.quantity} available)
                        </span>
                      </div>
                    )}
                  </div>

                  {/* Description */}
                  <div className="mb-8">
                    <h2 className="font-display text-3xl mb-2">
                      Description
                    </h2>
                    <p className="text-[#5b524a] leading-relaxed font-light">
                      {item.description}
                    </p>
                  </div>

                  {/* Specifications */}
                  <div className="bg-[#ede8dc] p-5 mb-8">
                    <h2 className="font-display text-3xl mb-4">
                      Specifications
                    </h2>
                    <div className="space-y-3">
                      <div className="flex items-start justify-between gap-4">
                        <span className="text-[#5b524a] shrink-0">Product ID:</span>
                        <span className="font-medium text-[#1f1a14] text-right break-all">
                          #{item.id}
                        </span>
                      </div>
                      <div className="flex items-start justify-between gap-4">
                        <span className="text-[#5b524a]">Available Quantity:</span>
                        <span className={`font-medium ${isOutOfStock ? 'text-[#7a3b2c]' : 'text-[#1f1a14]'}`}>
                          {item.quantity} units
                        </span>
                      </div>
                    </div>
                  </div>
                </div>

                {/* Action Buttons */}
                <div className="space-y-3">
                  <Button
                    variant="secondary"
                    size="lg"
                    fullWidth
                    disabled
                  >
                    {isOutOfStock ? 'Out of Stock' : 'Cart coming soon'}
                  </Button>
                  <Button
                    variant="secondary"
                    size="lg"
                    fullWidth
                    onClick={() => navigate('/items')}
                  >
                    Continue Shopping
                  </Button>
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    </main>
  );
}
